#ifndef BOARD_H
#define BOARD_H

#include <stdint.h>

/* Memory-mapped terminal: a byte written to TX is printed, a read of RX returns the last key
 * pressed on the page's keyboard (0 if none) and clears it. */
#define BOARD_TX    ( ( volatile uint8_t * ) 0x9000 )
#define BOARD_RX    ( ( volatile uint8_t * ) 0x9004 )

#define RAM_KB      ( RAM_SIZE / 1024 )

static inline int getkey( void )
{
    return *BOARD_RX;
}

static inline void putch( char c )
{
    *BOARD_TX = ( uint8_t ) c;
}

const char * term_write( const char * s, int stop );
void puts_raw( const char * s );
void xprintf( const char * fmt, ... );
int xsnprintf( char * buf, unsigned size, const char * fmt, ... );
void vRegisterCommands( void );

/* Adds a task to the list ps shows. The shell registers first, which keeps it out of kill's reach. */
void vRegisterTask( void * task );

#endif
