#include <stdint.h>

volatile int32_t debug_setpoint = 1000;
volatile int32_t debug_feedback = 0;
volatile int32_t debug_counter = 0;
volatile float debug_gain = 1.0f;
volatile int32_t debug_error = 0;
volatile uint32_t debug_flags = 0xA5A50001u;

uint32_t SystemCoreClock = 16000000u;

static int32_t clamp_i32(int32_t value, int32_t low, int32_t high);
static float clamp_f32(float value, float low, float high);
static void probe_tick(void);

void SystemInit(void)
{
    volatile uint32_t *cpacr = (volatile uint32_t *)0xE000ED88u;

    *cpacr |= (0xFu << 20);
}

int main(void)
{
    while (1) {
        probe_tick();
    }
}

static int32_t clamp_i32(int32_t value, int32_t low, int32_t high)
{
    if (value < low) {
        return low;
    }
    if (value > high) {
        return high;
    }
    return value;
}

static float clamp_f32(float value, float low, float high)
{
    if (value != value) {
        return 0.0f;
    }
    if (value < low) {
        return low;
    }
    if (value > high) {
        return high;
    }
    return value;
}

static void probe_tick(void)
{
    int32_t setpoint;
    int32_t feedback;
    int32_t error;
    float gain;

    setpoint = clamp_i32(debug_setpoint, -100000, 100000);
    feedback = clamp_i32(debug_feedback, -100000, 100000);
    gain = clamp_f32(debug_gain, -100.0f, 100.0f);
    error = setpoint - feedback;

    debug_error = error;
    debug_feedback = feedback + (int32_t)((float)error * gain * 0.001f);
    if (debug_counter >= INT32_MAX) {
        debug_counter = 0;
    } else {
        debug_counter++;
    }

    if (((uint32_t)debug_counter & 0x3FFu) == 0u) {
        debug_flags ^= 1u;
    }
}
