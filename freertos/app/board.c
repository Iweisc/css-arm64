/* Console output, a small printf and the C library functions FreeRTOS needs. */
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

#include "board.h"

/* Every instruction costs a visible fraction of a second on this CPU, so output keeps its state
 * in registers: buf is NULL for the terminal, and n counts characters produced. */
static inline __attribute__( ( always_inline ) ) void emit( char * buf, unsigned size, unsigned * n, char c )
{
    if( buf == NULL )
    {
        putch( c );
    }
    else if( *n + 1 < size )
    {
        buf[ *n ] = c;
    }

    ( *n )++;
}

/* Supports %s %c %d %u %x with an optional width and '-' flag. */
static unsigned format( char * buf, unsigned size, const char * fmt, va_list ap )
{
    unsigned n = 0;

    for( ; ; )
    {
        char c;

        /* Literal text, most of all output, in the shortest loop we can. The terminal doesn't
         * need n counted. */
        if( buf == NULL )
        {
            fmt = term_write( fmt, '%' );
            c = *fmt;
        }
        else
        {
            for( ; ( c = *fmt ) != 0 && c != '%'; fmt++ )
            {
                emit( buf, size, &n, c );
            }
        }

        if( c == 0 )
        {
            return n;
        }

        int left = 0, width = 0;
        fmt++;

        if( *fmt == '-' )
        {
            left = 1;
            fmt++;
        }

        while( *fmt >= '0' && *fmt <= '9' )
        {
            width = width * 10 + ( *fmt++ - '0' );
        }

        char tmp[ 24 ];
        const char * str = tmp;
        int len = 0;

        if( *fmt == 's' )
        {
            str = va_arg( ap, const char * );

            while( str[ len ] )
            {
                len++;
            }
        }
        else if( *fmt == 'c' )
        {
            tmp[ 0 ] = ( char ) va_arg( ap, int );
            len = 1;
        }
        else
        {
            unsigned long v;
            unsigned base = ( *fmt == 'x' ) ? 16 : 10;
            int neg = 0;

            if( *fmt == 'd' )
            {
                long sv = va_arg( ap, int );
                neg = sv < 0;
                v = neg ? ( unsigned long ) -sv : ( unsigned long ) sv;
            }
            else
            {
                v = va_arg( ap, unsigned );
            }

            char * p = tmp + sizeof( tmp );

            do
            {
                *--p = "0123456789abcdef"[ v % base ];
                v /= base;
            } while( v );

            if( neg )
            {
                *--p = '-';
            }

            str = p;
            len = ( int ) ( tmp + sizeof( tmp ) - p );
        }

        fmt++;
        int pad = width > len ? width - len : 0;

        while( !left && pad-- > 0 )
        {
            emit( buf, size, &n, ' ' );
        }

        for( int i = 0; i < len; i++ )
        {
            emit( buf, size, &n, str[ i ] );
        }

        while( left && pad-- > 0 )
        {
            emit( buf, size, &n, ' ' );
        }
    }
}

void xprintf( const char * fmt, ... )
{
    va_list ap;

    va_start( ap, fmt );
    format( NULL, 0, fmt, ap );
    va_end( ap );
}

int xsnprintf( char * buf, unsigned size, const char * fmt, ... )
{
    va_list ap;

    va_start( ap, fmt );
    unsigned n = format( buf, size, fmt, ap );
    va_end( ap );
    buf[ n < size ? n : size - 1 ] = 0;
    return ( int ) n;
}

/* The CLI ends lines with "\r\n". The terminal treats '\n' as a full newline, so drop '\r'. */
void puts_raw( const char * s )
{
    while( *( s = term_write( s, '\r' ) ) )
    {
        s++;
    }
}

void * memset( void * d, int c, size_t n )
{
    uint8_t * p = d;

    /* Task creation fills whole stacks, so aligned runs go 8 bytes per store. */
    if( ( ( uintptr_t ) p & 7 ) == 0 )
    {
        uint64_t v = ( uint8_t ) c * 0x0101010101010101ULL;

        for( ; n >= 32; n -= 32, p += 32 )
        {
            ( ( uint64_t * ) p )[ 0 ] = v;
            ( ( uint64_t * ) p )[ 1 ] = v;
            ( ( uint64_t * ) p )[ 2 ] = v;
            ( ( uint64_t * ) p )[ 3 ] = v;
        }

        for( ; n >= 8; n -= 8, p += 8 )
        {
            *( uint64_t * ) p = v;
        }
    }

    while( n-- )
    {
        *p++ = ( uint8_t ) c;
    }

    return d;
}

void * memcpy( void * d, const void * s, size_t n )
{
    uint8_t * p = d;
    const uint8_t * q = s;

    while( n-- )
    {
        *p++ = *q++;
    }

    return d;
}

size_t strlen( const char * s )
{
    size_t n = 0;

    while( s[ n ] )
    {
        n++;
    }

    return n;
}

int strncmp( const char * a, const char * b, size_t n )
{
    for( ; n; n--, a++, b++ )
    {
        if( *a != *b || !*a )
        {
            return ( uint8_t ) *a - ( uint8_t ) *b;
        }
    }

    return 0;
}

char * strncpy( char * d, const char * s, size_t n )
{
    size_t i = 0;

    for( ; i < n && s[ i ]; i++ )
    {
        d[ i ] = s[ i ];
    }

    for( ; i < n; i++ )
    {
        d[ i ] = 0;
    }

    return d;
}
