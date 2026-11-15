/* Shell commands, registered with FreeRTOS+CLI. "help" comes with the CLI. */
#include <stdint.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"
#include "FreeRTOS_CLI.h"
#include "board.h"

#define MAX_TASKS    6
/* printf frames plus the 288-byte saved context need more than configMINIMAL_STACK_SIZE. */
#define TASK_STACK   100

static unsigned parse_uint( const char * s, BaseType_t len )
{
    unsigned v = 0;

    for( BaseType_t i = 0; i < len && s[ i ] >= '0' && s[ i ] <= '9'; i++ )
    {
        v = v * 10 + ( unsigned ) ( s[ i ] - '0' );
    }

    return v;
}

static const char * arg( const char * cmd, BaseType_t * len )
{
    const char * p = FreeRTOS_CLIGetParameter( cmd, 1, len );

    return p ? p : "";
}

/* The tasks ps lists. uxTaskGetSystemState would find them too, but it needs the trace facility,
 * which fills every new stack with a pattern and measures it a byte at a time: thousands of
 * instructions, which is minutes on this CPU. */
static TaskHandle_t xTasks[ MAX_TASKS ];

void vRegisterTask( void * t )
{
    for( unsigned i = 0; i < MAX_TASKS; i++ )
    {
        if( xTasks[ i ] == NULL )
        {
            xTasks[ i ] = t;
            return;
        }
    }
}

static void prvForgetTask( TaskHandle_t t )
{
    for( unsigned i = 0; i < MAX_TASKS; i++ )
    {
        if( xTasks[ i ] == t )
        {
            xTasks[ i ] = NULL;
        }
    }
}

static void prvExit( void )
{
    prvForgetTask( xTaskGetCurrentTaskHandle() );
    vTaskDelete( NULL );
}

static BaseType_t ps( char * out, size_t size, const char * cmd )
{
    static unsigned next;
    static const char states[] = "RrBSDi";

    ( void ) cmd;

    if( next == 0 )
    {
        xsnprintf( out, size, " id name     state prio\n" );
        next = 1;
        return pdTRUE;
    }

    /* Slot MAX_TASKS stands for the idle task, which the kernel creates. */
    while( next <= MAX_TASKS && xTasks[ next - 1 ] == NULL )
    {
        next++;
    }

    TaskHandle_t h = next <= MAX_TASKS ? xTasks[ next - 1 ] : xTaskGetIdleTaskHandle();
    xsnprintf( out, size, "%3u %-8s %c     %u\n", next, pcTaskGetName( h ), states[ eTaskGetState( h ) ],
               ( unsigned ) uxTaskPriorityGet( h ) );

    if( next++ <= MAX_TASKS )
    {
        return pdTRUE;
    }

    next = 0;
    return pdFALSE;
}

static BaseType_t uptime( char * out, size_t size, const char * cmd )
{
    uint64_t steps;

    ( void ) cmd;
    __asm volatile ( "mrs %0, cntpct_el0" : "=r" ( steps ) );
    xsnprintf( out, size, "%u ticks, %u steps\n", ( unsigned ) xTaskGetTickCount(), ( unsigned ) steps );
    return pdFALSE;
}

static BaseType_t mem( char * out, size_t size, const char * cmd )
{
    ( void ) cmd;
    xsnprintf( out, size, "heap: %u of %u bytes free\n", ( unsigned ) xPortGetFreeHeapSize(),
               ( unsigned ) configTOTAL_HEAP_SIZE );
    return pdFALSE;
}

static BaseType_t echo( char * out, size_t size, const char * cmd )
{
    const char * p = cmd + 4;

    while( *p == ' ' )
    {
        p++;
    }

    xsnprintf( out, size, "%s\n", p );
    return pdFALSE;
}

static BaseType_t clear( char * out, size_t size, const char * cmd )
{
    ( void ) cmd;
    xsnprintf( out, size, "\f" );
    return pdFALSE;
}

static void primes_task( void * param )
{
    unsigned limit = ( unsigned ) ( uintptr_t ) param;

    for( unsigned n = 2; n <= limit; n++ )
    {
        unsigned d = 2;

        while( d * d <= n && n % d )
        {
            d++;
        }

        if( d * d > n )
        {
            xprintf( "%u ", n );
        }
    }

    xprintf( "\n[primes done]\n" );
    prvExit();
}

static void count_task( void * param )
{
    unsigned limit = ( unsigned ) ( uintptr_t ) param;

    for( unsigned n = 1; n <= limit; n++ )
    {
        xprintf( "[tick %u: count %u]\n", ( unsigned ) xTaskGetTickCount(), n );
        vTaskDelay( 1 );
    }

    prvExit();
}

static BaseType_t spawn( char * out, size_t size, const char * name, TaskFunction_t fn, unsigned n, UBaseType_t prio )
{
    TaskHandle_t t;
    BaseType_t ok = xTaskCreate( fn, name, TASK_STACK, ( void * ) ( uintptr_t ) n, prio, &t );

    if( ok == pdPASS )
    {
        vRegisterTask( t );
    }

    xsnprintf( out, size, ok == pdPASS ? "started %s\n" : "out of memory\n", name );
    return pdFALSE;
}

static BaseType_t primes( char * out, size_t size, const char * cmd )
{
    BaseType_t len;
    const char * a = arg( cmd, &len );

    return spawn( out, size, "primes", primes_task, len ? parse_uint( a, len ) : 50, tskIDLE_PRIORITY + 1 );
}

static BaseType_t count( char * out, size_t size, const char * cmd )
{
    BaseType_t len;
    const char * a = arg( cmd, &len );

    return spawn( out, size, "count", count_task, len ? parse_uint( a, len ) : 3, tskIDLE_PRIORITY + 1 );
}

static BaseType_t kill( char * out, size_t size, const char * cmd )
{
    static char name[ configMAX_TASK_NAME_LEN ];
    BaseType_t len;
    const char * a = arg( cmd, &len );

    if( len >= configMAX_TASK_NAME_LEN )
    {
        len = configMAX_TASK_NAME_LEN - 1;
    }

    memcpy( name, a, ( size_t ) len );
    name[ len ] = 0;
    TaskHandle_t t = NULL;

    /* Slot 0 is the shell, which must not go. */
    for( unsigned i = 1; i < MAX_TASKS; i++ )
    {
        if( xTasks[ i ] && strncmp( pcTaskGetName( xTasks[ i ] ), name, configMAX_TASK_NAME_LEN ) == 0 )
        {
            t = xTasks[ i ];
        }
    }

    if( t == NULL )
    {
        xsnprintf( out, size, "kill: no task %s\n", name );
    }
    else
    {
        prvForgetTask( t );
        vTaskDelete( t );
        xsnprintf( out, size, "killed %s\n", name );
    }

    return pdFALSE;
}

static const CLI_Command_Definition_t commands[] =
{
    { "ps",     "ps: list tasks\n",                      ps,     0  },
    { "uptime", "uptime: ticks and steps since boot\n",  uptime, 0  },
    { "free",   "free: free heap bytes\n",               mem,    0  },
    { "echo",   "echo <text>: print text\n",             echo,   -1 },
    { "primes", "primes [n]: background primes task\n",  primes, -1 },
    { "count",  "count [n]: count once per tick\n",      count,  -1 },
    { "kill",   "kill <task>: delete a task\n",          kill,   1  },
    { "clear",  "clear: clear the screen\n",             clear,  0  },
};

#define NCOMMANDS    ( sizeof( commands ) / sizeof( commands[ 0 ] ) )

void vRegisterCommands( void )
{
    static CLI_Definition_List_Item_t items[ NCOMMANDS ];

    for( unsigned i = 0; i < NCOMMANDS; i++ )
    {
        FreeRTOS_CLIRegisterCommandStatic( &commands[ i ], &items[ i ] );
    }
}
