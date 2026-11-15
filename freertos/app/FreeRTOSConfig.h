#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

/* One tick is TICK_STEPS instruction steps of the CSS machine. configTICK_RATE_HZ is nominal:
 * the machine runs about 25 steps per second in a browser, so a tick is really ~20 s. */
#define TICK_STEPS                                 2000

#define configUSE_PREEMPTION                       1
#define configUSE_TIME_SLICING                     1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION    1
#define configUSE_IDLE_HOOK                        0
#define configUSE_TICK_HOOK                        0
#define configIDLE_SHOULD_YIELD                    1
#define configTICK_RATE_HZ                         1000
#define configMAX_PRIORITIES                       4
#define configMINIMAL_STACK_SIZE                   64
#define configMAX_TASK_NAME_LEN                    8
#define configTICK_TYPE_WIDTH_IN_BITS              TICK_TYPE_WIDTH_64_BITS
#define configUSE_MUTEXES                          0
#define configUSE_RECURSIVE_MUTEXES                0
#define configUSE_COUNTING_SEMAPHORES              0
#define configUSE_QUEUE_SETS                       0
#define configUSE_TASK_NOTIFICATIONS               1
#define configTASK_NOTIFICATION_ARRAY_ENTRIES      1
#define configQUEUE_REGISTRY_SIZE                  0
#define configUSE_TIMERS                           0
#define configUSE_CO_ROUTINES                      0
#define configUSE_TRACE_FACILITY                   0
#define configUSE_STATS_FORMATTING_FUNCTIONS       0
#define configGENERATE_RUN_TIME_STATS              0
#define configCHECK_FOR_STACK_OVERFLOW             0
#define configUSE_MALLOC_FAILED_HOOK               0
#define configSUPPORT_DYNAMIC_ALLOCATION           1
#define configSUPPORT_STATIC_ALLOCATION            1
#define configTOTAL_HEAP_SIZE                      ( 3 * 1024 )
#define configKERNEL_PROVIDED_STATIC_MEMORY        1
#define configENABLE_BACKWARD_COMPATIBILITY        0
#define configUSE_NEWLIB_REENTRANT                 0
#define configUSE_TASK_FPU_SUPPORT                 1

#define INCLUDE_vTaskDelete                        1
#define INCLUDE_vTaskDelay                         1
#define INCLUDE_vTaskSuspend                       1
#define INCLUDE_xTaskGetHandle                     0
#define INCLUDE_xTaskGetIdleTaskHandle             1
#define INCLUDE_uxTaskPriorityGet                  1
#define INCLUDE_vTaskPrioritySet                   0
#define INCLUDE_xTaskGetSchedulerState             0
#define INCLUDE_xTaskGetCurrentTaskHandle          1
#define INCLUDE_uxTaskGetStackHighWaterMark        0
#define INCLUDE_xTaskDelayUntil                    0
#define INCLUDE_eTaskGetState                      1

/* The machine's GIC CPU interface implements 5 priority bits. The timer (PPI 30) sits at
 * the lowest usable priority, 0xF0, as the port requires of the tick interrupt. */
#define configUNIQUE_INTERRUPT_PRIORITIES          32
#define configMAX_API_CALL_INTERRUPT_PRIORITY      18

void vConfigureTickInterrupt( void );
void vClearTickInterrupt( void );
#define configSETUP_TICK_INTERRUPT()               vConfigureTickInterrupt()
#define configCLEAR_TICK_INTERRUPT()               vClearTickInterrupt()

#define configCOMMAND_INT_MAX_OUTPUT_SIZE          160

#endif
