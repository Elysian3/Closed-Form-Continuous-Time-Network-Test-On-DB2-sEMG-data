// Run the actual exported model on synthetic normalized inputs before USB starts.
// Electrode inference remains blocked: uV conversion and frozen ZC thresholds
// have not been established. This self-test cannot certify motion or latency.
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "autoncp.h"
#ifdef AUTONCP_INTEGER_FC
#include "integer_self_test.h"
#else
#include "autoncp_self_test.h"
#endif
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#ifdef AUTONCP_BENCHMARK
#include "profile_vectors.h"
#include "ads1298_protocol.h"
#include "esp_mmu_map.h"
#include "esp_psram.h"

typedef struct {
    int64_t total_us;
    int64_t fc_us[AUTONCP_LAYER_COUNT][4];
    int64_t activation_us[AUTONCP_LAYER_COUNT];
    int64_t head_us;
    int64_t quantize_us[AUTONCP_LAYER_COUNT + 1];
} step_timing_t;
static step_timing_t timings[PROFILE_STEPS];
static int64_t auxiliary[PROFILE_STEPS][5];
static float reference_window[400][8];

#ifdef AUTONCP_PROFILE
int64_t autoncp_profile_now_us(void) { return esp_timer_get_time(); }
#endif

/* Test-only scalar definitions, not a deployed DSP pipeline. */
static void reference_rms(float output[8])
{
    for (int channel = 0; channel < 8; ++channel) {
        float sum = 0;
        for (int sample = 0; sample < 400; ++sample)
            sum += reference_window[sample][channel] * reference_window[sample][channel];
        output[channel] = sqrtf(sum / 400.0f);
    }
}

static void reference_zc(unsigned output[8])
{
    for (int channel = 0; channel < 8; ++channel) {
        unsigned count = 0;
        for (int sample = 1; sample < 400; ++sample) {
            float left = reference_window[sample - 1][channel];
            float right = reference_window[sample][channel];
            count += left * right < 0 && fabsf(left - right) >= 5.0f;
        }
        output[channel] = count;
    }
}

static bool benchmark_sequence(bool enabled)
{
#ifdef AUTONCP_PROFILE
    autoncp_profile_enable(enabled);
#endif
    float input[AUTONCP_INPUT_SIZE], output[AUTONCP_OUTPUT_SIZE];
    float errors[AUTONCP_OUTPUT_SIZE] = {0};
    autoncp_reset();
    memset(timings, 0, sizeof(timings));
    for (int step = 0; step < PROFILE_STEPS; ++step) {
        /* Fixture rodata may move with PSRAM; model inputs always start in DRAM. */
        memcpy(input, profile_inputs[step], sizeof(input));
        int64_t started = esp_timer_get_time();
        bool ok = autoncp_step(input, output);
        timings[step].total_us = esp_timer_get_time() - started;
        if (!ok) return false;
#ifdef AUTONCP_PROFILE
        if (enabled) {
            const autoncp_profile_t *last = autoncp_profile_last();
            memcpy(timings[step].fc_us, last->fc_us, sizeof(last->fc_us));
            memcpy(timings[step].activation_us, last->activation_us, sizeof(last->activation_us));
            timings[step].head_us = last->head_us;
            memcpy(timings[step].quantize_us, last->quantize_us, sizeof(last->quantize_us));
        }
#endif
        for (int target = 0; target < AUTONCP_OUTPUT_SIZE; ++target) {
            float error = fabsf(output[target] - profile_outputs[step][target]);
            if (!isfinite(error) || error > 0.0002f) return false;
            if (error > errors[target]) errors[target] = error;
        }
        vTaskDelay(1);
    }
    float state_error = 0;
    for (int state = 0; state < AUTONCP_STATE_SIZE; ++state) {
        float error = fabsf(autoncp_state()[state] - profile_final_state[state]);
        if (!isfinite(error) || error > 0.0002f) return false;
        if (error > state_error) state_error = error;
    }
    /* Emit only after all intervals; never write text inside a timed sequence. */
    const char *mode = enabled ? "stages" : "control";
    for (int step = 0; step < PROFILE_STEPS; ++step) {
        printf("PROFILE_STEP mode=%s i=%d total=%lld fc=", mode, step, (long long)timings[step].total_us);
        for (int layer = 0; layer < AUTONCP_LAYER_COUNT; ++layer)
            for (int kind = 0; kind < 4; ++kind)
                printf("%s%lld", layer || kind ? "," : "", (long long)timings[step].fc_us[layer][kind]);
        printf(" act=");
        for (int layer = 0; layer < AUTONCP_LAYER_COUNT; ++layer)
            printf("%s%lld", layer ? "," : "", (long long)timings[step].activation_us[layer]);
        printf(" head=%lld quant=", (long long)timings[step].head_us);
        for (int i = 0; i <= AUTONCP_LAYER_COUNT; ++i)
            printf("%s%lld", i ? "," : "", (long long)timings[step].quantize_us[i]);
        printf("\n");
    }
    printf("PROFILE_NUMERIC mode=%s state=%.9g targets=", mode, (double)state_error);
    for (int target = 0; target < AUTONCP_OUTPUT_SIZE; ++target)
        printf("%s%.9g", target ? "," : "", (double)errors[target]);
    printf("\n");
    return true;
}

static bool benchmark_auxiliary(void)
{
    float features[16], normalized[16], target[10], restored[10], rms[8];
    unsigned zc[8];
    ads1298_frame_t scans[7] = {0};
    uint8_t packet[ADS1298_PC_MAX_EMG_PACKET_BYTES];
    float normalize_error = 0, inverse_relative_error = 0;
    for (int sample = 0; sample < 400; ++sample)
        for (int channel = 0; channel < 8; ++channel)
            reference_window[sample][channel] = (float)((sample % 40) - 20) * (channel + 1);
    for (int step = 0; step < PROFILE_STEPS; ++step) {
        memcpy(features, profile_features[step], sizeof(features));
        memcpy(target, profile_outputs[step], sizeof(target));
        /* Change fixtures outside the interval so the compiler cannot hoist kernels. */
        reference_window[0][0] = step % 2 ? -19.0f : -20.0f;
        scans[0].channels[0] = step;
        int64_t started = esp_timer_get_time();
        bool ok = autoncp_normalize_features(features, normalized);
        auxiliary[step][0] = esp_timer_get_time() - started;
        if (!ok) return false;
        started = esp_timer_get_time();
        ok = autoncp_inverse_targets(target, restored);
        auxiliary[step][1] = esp_timer_get_time() - started;
        if (!ok) return false;
        started = esp_timer_get_time();
        reference_rms(rms);
        auxiliary[step][2] = esp_timer_get_time() - started;
        started = esp_timer_get_time();
        reference_zc(zc);
        auxiliary[step][3] = esp_timer_get_time() - started;
        started = esp_timer_get_time();
        size_t bytes = ads1298_pack_emg_frame(scans, 7, 6, packet, sizeof(packet));
        auxiliary[step][4] = esp_timer_get_time() - started;
        if (bytes != 229 || packet[0] != 0xa5 || packet[228] != 0x5a) return false;
        for (int channel = 0; channel < 8; ++channel) {
            float mean_square = 133.5f * (channel + 1) * (channel + 1);
            if (channel == 0 && step % 2) mean_square -= 39.0f / 400.0f;
            if (fabsf(rms[channel] - sqrtf(mean_square)) > 0.00002f || zc[channel] != 9) return false;
        }
        for (int feature = 0; feature < 16; ++feature) {
            float error = fabsf(normalized[feature] - profile_inputs[step][feature]);
            if (!isfinite(error) || error > 0.0002f) return false;
            if (error > normalize_error) normalize_error = error;
        }
        for (int output = 0; output < 10; ++output) {
            float error = fabsf(restored[output] - profile_targets[step][output]) /
                          fmaxf(1.0f, fabsf(profile_targets[step][output]));
            if (!isfinite(error) || error > 0.0002f) return false;
            if (error > inverse_relative_error) inverse_relative_error = error;
        }
        vTaskDelay(1);
    }
    for (int step = 0; step < PROFILE_STEPS; ++step)
        printf("PROFILE_AUX i=%d us=%lld,%lld,%lld,%lld,%lld\n", step,
               (long long)auxiliary[step][0], (long long)auxiliary[step][1],
               (long long)auxiliary[step][2], (long long)auxiliary[step][3], (long long)auxiliary[step][4]);
    printf("PROFILE_AUX_NUMERIC normalize=%.9g inverse_relative=%.9g rms_zc=pass packet=pass\n",
           (double)normalize_error, (double)inverse_relative_error);
    return true;
}

static bool run_latency_benchmark(void)
{
    printf("PROFILE_VARIANT padded=%d fused_gate_bits=%d blocked=%d\n",
           AUTONCP_PADDED_ROWS, AUTONCP_FUSED_GATE_BITS, AUTONCP_BLOCKED_FC);
#ifdef AUTONCP_INTEGER_FC
#ifdef AUTONCP_INTEGER_SIMD
    printf("PROFILE_KERNEL kind=int8_simd\n");
#else
    printf("PROFILE_KERNEL kind=int8_scalar\n");
#endif
#else
    printf("PROFILE_KERNEL kind=fp32\n");
#endif
    esp_paddr_t physical;
    mmu_target_t target;
    const void *weight = autoncp_benchmark_weight_address();
    if (esp_mmu_vaddr_to_paddr((void *)weight, &physical, &target) != ESP_OK) return false;
#if CONFIG_SPIRAM_RODATA
    if (target != MMU_TARGET_PSRAM0) return false;
#else
    if (target != MMU_TARGET_FLASH0) return false;
#endif
    printf("PROFILE_META steps=%d warmup=%d instrumented=%d corpus=%s\n", PROFILE_STEPS, PROFILE_WARMUP,
#ifdef AUTONCP_PROFILE
           1,
#else
           0,
#endif
           PROFILE_CORPUS_SHA256);
    size_t psram_bytes = 0;
#if CONFIG_SPIRAM
    psram_bytes = esp_psram_is_initialized() ? esp_psram_get_size() : 0;
#endif
    printf("PROFILE_MEMORY weight=%p physical=0x%lx target=%s psram=%u\n", weight,
           (unsigned long)physical, target == MMU_TARGET_PSRAM0 ? "PSRAM" : "FLASH",
           (unsigned)psram_bytes);
    int64_t sum = 0, maximum = 0;
    for (int i = 0; i < 256; ++i) {
        int64_t started = esp_timer_get_time();
        int64_t elapsed = esp_timer_get_time() - started;
        sum += elapsed;
        if (elapsed > maximum) maximum = elapsed;
    }
    printf("PROFILE_CLOCK n=256 sum=%lld max=%lld\n", (long long)sum, (long long)maximum);
    if (!benchmark_sequence(false)) return false;
#ifdef AUTONCP_PROFILE
    if (!benchmark_sequence(true)) return false;
    autoncp_profile_enable(false);
#endif
    if (!benchmark_auxiliary()) return false;
    autoncp_reset();
    printf("PROFILE_DONE\n");
    return true;
}
#endif

bool antikythera_model_boot_check(void)
{
#ifdef AUTONCP_INTEGER_SIMD
    if (!autoncp_integer_dot_self_test()) {
        printf("PROFILE_FAILED SIMD dot self-test\n");
        return false;
    }
    printf("SIMD_DOT_TEST lengths=1,10,15,16,17,31,32,313,471,758 patterns=3 result=pass\n");
#endif
    float output[AUTONCP_OUTPUT_SIZE];
    float max_error = 0.0f;
    int64_t max_step_us = 0;
    autoncp_reset();
    for (int step = 0; step < AUTONCP_SELF_TEST_STEPS; ++step) {
        const int64_t started = esp_timer_get_time();
        if (!autoncp_step(&model_self_test_inputs[step * AUTONCP_INPUT_SIZE], output)) {
            printf("AutoNCP self-test rejected step %d\n", step);
            return false;
        }
        const int64_t elapsed = esp_timer_get_time() - started;
        if (elapsed > max_step_us) max_step_us = elapsed;
        for (int target = 0; target < AUTONCP_OUTPUT_SIZE; ++target) {
            const float expected = model_self_test_outputs[step * AUTONCP_OUTPUT_SIZE + target];
            const float error = fabsf(output[target] - expected);
            if (!isfinite(error) || error > 0.0002f) {
                printf("AutoNCP self-test mismatch: step=%d target=%d error=%g\n",
                       step, target, (double)error);
                return false;
            }
            if (error > max_error) max_error = error;
        }
        vTaskDelay(1); // Allow the idle task to run during the startup check.
    }
    autoncp_reset();
    printf("AutoNCP sha256=%s weight_bits=%d states=%d outputs=%d\n",
           AUTONCP_CHECKPOINT_SHA256, AUTONCP_WEIGHT_BITS, AUTONCP_STATE_SIZE, AUTONCP_OUTPUT_SIZE);
    printf("Synthetic self-test passed: max_error=%g max_step_us=%lld\n",
           (double)max_error, (long long)max_step_us);
    printf("Electrode inference BLOCKED: unit evidence and target ZC thresholds missing.\n");
#ifdef AUTONCP_BENCHMARK
    if (!run_latency_benchmark()) {
        printf("PROFILE_FAILED\n");
        return false;
    }
    /* A compute-only image must not proceed into ADS1298 acquisition. */
    for (;;) vTaskDelay(1);
#endif
    return true;
}
