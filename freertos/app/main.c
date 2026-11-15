/* FreeRTOS demo for the CSS machine: a FreeRTOS+CLI shell plus background tasks. */
#include <stdint.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"
#include "FreeRTOS_CLI.h"
#include "board.h"

#define TIMER_INTID     30
#define KBD_INTID       33
#define SHELL_STACK     96

static void prvShellTask( void * pvParameters );

/* The keyboard interrupt edits and echoes the line itself, like a tty in canonical mode, and
 * wakes the shell only on Enter. Waking a task costs hundreds of instructions, which is many
 * seconds on this CPU, so doing it once per line keeps typing responsive. */
static TaskHandle_t xShell;
static char cLine[ 48 ];
static volatile size_t xLineLen;
static volatile int xLineReady;

int main( void )
{
    static StaticTask_t xShellTCB;
    static StackType_t uxShellStack[ SHELL_STACK ];

    xprintf( "\fFreeRTOS Kernel %s\n", tskKERNEL_VERSION_NUMBER );
    xprintf( "tick every %u steps\n", TICK_STEPS );
    vRegisterCommands();
    xShell = xTaskCreateStatic( prvShellTask, "shell", SHELL_STACK, NULL, tskIDLE_PRIORITY + 2, uxShellStack,
                                &xShellTCB );
    vRegisterTask( xShell );
    vTaskStartScheduler();
    return 0;
}

static void prvShellTask( void * pvParameters )
{
    static char out[ configCOMMAND_INT_MAX_OUTPUT_SIZE ];

    ( void ) pvParameters;
    xprintf( "type help for commands\n" );

    for( ; ; )
    {
        xprintf( "$ " );
        xLineLen = 0;
        xLineReady = 0;
        ulTaskNotifyTake( pdTRUE, portMAX_DELAY );

        if( cLine[ 0 ] != 0 )
        {
            BaseType_t more;

            do
            {
                more = FreeRTOS_CLIProcessCommand( cLine, out, sizeof( out ) );
                puts_raw( out );
            } while( more != pdFALSE );
        }
    }
}

static void prvKeyInterrupt( char c )
{
    BaseType_t xWoken = pdFALSE;

    if( xLineReady )
    {
        return;     /* the shell is still running the last line */
    }

    if( c == '\b' )
    {
        if( xLineLen > 0 )
        {
            xLineLen--;
            puts_raw( "\b \b" );
        }
    }
    else if( c == '\n' )
    {
        putch( c );
        cLine[ xLineLen ] = 0;
        xLineReady = 1;
        vTaskNotifyGiveFromISR( xShell, &xWoken );
    }
    else if( ( c >= ' ' ) && ( xLineLen < sizeof( cLine ) - 1 ) )
    {
        cLine[ xLineLen++ ] = c;
        putch( c );
    }

    portYIELD_FROM_ISR( xWoken );
}

/* The timer drives the tick. */
void vConfigureTickInterrupt( void )
{
    vClearTickInterrupt();
    __asm volatile ( "msr cntp_ctl_el0, %0" ::"r" ( 1UL ) );
}

void vClearTickInterrupt( void )
{
    __asm volatile ( "msr cntp_tval_el0, %0" ::"r" ( ( uint64_t ) TICK_STEPS ) );
}

/* The port reads the INTID from the GIC and passes it here. */
void vApplicationIRQHandler( uint32_t ulICCIAR )
{
    uint32_t id = ulICCIAR & 0x3FF;

    if( id == TIMER_INTID )
    {
        FreeRTOS_Tick_Handler();
    }
    else if( id == KBD_INTID )
    {
        /* Each read takes the oldest key. Taking every waiting key here costs one interrupt
         * entry for all of them, and the interrupt deasserts once the keyboard is empty. */
        char c;

        while( ( c = ( char ) getkey() ) != 0 )
        {
            prvKeyInterrupt( c );
        }
    }
}

void vFault( uint64_t esr, uint64_t elr )
{
    xprintf( "\nfault esr=%x elr=%x\n", ( unsigned ) esr, ( unsigned ) elr );

    for( ; ; )
    {
    }
}
