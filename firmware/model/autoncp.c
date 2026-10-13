#include "autoncp.h"

#ifdef AUTONCP_INTEGER_SIMD
#ifndef ESP_PLATFORM
#error "The experimental SIMD dot requires ESP-IDF"
#endif
#include "sdkconfig.h"
#if !defined(CONFIG_IDF_TARGET_ESP32S3)
#error "The experimental SIMD dot requires ESP32-S3"
#endif
#include "freertos/FreeRTOS.h"
#endif

#include <math.h>
#include <float.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* This definition is deliberately before the generated header: its exported
 * model_layers table uses the type while keeping all weights const in flash. */
typedef struct {
    int inputs;
    int units;
    int state_offset;
    const void *weight[4];
    const float *scale[4];
    const float *bias[4];
} autoncp_layer_t;

#include "autoncp_weights.h"

#ifdef AUTONCP_BENCHMARK
const void *autoncp_benchmark_weight_address(void) {
    return model_layers[0].weight[0];
}
#endif

enum {
    AUTONCP_FF1 = 0,
    AUTONCP_FF2 = 1,
    AUTONCP_TIME_A = 2,
    AUTONCP_TIME_B = 3,
};

static float model_state[AUTONCP_STATE_SIZE];
// Single-owner runtime: keep scratch off the ESP task stack. Commit state only
// after a complete finite step, so an invalid input cannot poison later steps.
static float next_state[AUTONCP_STATE_SIZE];
static float concat[AUTONCP_MAX_CONCAT];
static float ff1[AUTONCP_MAX_UNITS];
static float ff2[AUTONCP_MAX_UNITS];
static float time_a[AUTONCP_MAX_UNITS];
#if !AUTONCP_FUSED_GATE_BITS
static float time_b[AUTONCP_MAX_UNITS];
#endif

#ifdef AUTONCP_INTEGER_FC
#if AUTONCP_WEIGHT_BITS != 8
#error "The experimental integer FC requires INT8 weights"
#endif
static int8_t integer_input[(AUTONCP_MAX_CONCAT + 15) & ~15] __attribute__((aligned(16)));
static float integer_input_scale;
#ifdef AUTONCP_INTEGER_SIMD
static int8_t integer_weights[(AUTONCP_MAX_CONCAT + 15) & ~15] __attribute__((aligned(16)));
static portMUX_TYPE integer_dot_lock = portMUX_INITIALIZER_UNLOCKED;
/* Accumulate the entire padded row in ACCX before reading its low 32 bits.
 * The INT8 bound below fits int32; all pointers are 16-byte aligned.
 * See Espressif ESP-NN src/common/esp_nn_dot_s8_esp32s3.S. */
static int32_t integer_dot(const int8_t *left, const int8_t *right, int count) {
    int32_t dot;
    int blocks = count / 16;
    portENTER_CRITICAL(&integer_dot_lock);
    __asm__ volatile (
        "ee.zero.accx\n"
        "1:\n"
        "ee.vld.128.ip q0, %[x], 16\n"
        "ee.vld.128.ip q1, %[w], 16\n"
        "ee.vmulas.s8.accx q0, q1\n"
        "addi %[blocks], %[blocks], -1\n"
        "bnez %[blocks], 1b\n"
        "nop\n"
        "nop\n"
        "rur.accx_0 %[dot]\n"
        : [dot] "=r" (dot), [x] "+r" (left), [w] "+r" (right),
          [blocks] "+r" (blocks)
        : : "memory");
    portEXIT_CRITICAL(&integer_dot_lock);
    return dot;
}
#ifdef AUTONCP_BENCHMARK
bool autoncp_integer_dot_self_test(void) {
    const int sizes[] = {1, 10, 15, 16, 17, 31, 32, 313, 471, 758};
    for (unsigned test = 0; test < sizeof(sizes) / sizeof(sizes[0]); ++test) {
        const int count = sizes[test], padded = (count + 15) & ~15;
        if (count > AUTONCP_MAX_CONCAT) continue;
        for (int pattern = 0; pattern < 3; ++pattern) {
            int32_t expected = 0;
            memset(integer_input, 0, padded);
            memset(integer_weights, 0, padded);
            for (int i = 0; i < count; ++i) {
                integer_input[i] = pattern == 0 ? 127 : pattern == 1 ? -127 : (i * 37 + 11) % 255 - 127;
                integer_weights[i] = pattern < 2 ? 127 : (i * 29 + 5) % 255 - 127;
                expected += (int32_t)integer_input[i] * (int32_t)integer_weights[i];
            }
            if (integer_dot(integer_input, integer_weights, padded) != expected) return false;
        }
    }
    return true;
}
#endif
#endif
#endif

#ifdef AUTONCP_PROFILE
static bool profile_enabled;
static autoncp_profile_t profile;
void autoncp_profile_enable(bool enabled) { profile_enabled = enabled; }
const autoncp_profile_t *autoncp_profile_last(void) { return &profile; }
#define PROFILE_START(name) \
    const int64_t name = profile_enabled ? autoncp_profile_now_us() : 0
#define PROFILE_END(destination, name) \
    do { if (profile_enabled) destination = autoncp_profile_now_us() - name; } while (0)
#else
#define PROFILE_START(name)
#define PROFILE_END(destination, name)
#endif

static bool all_finite(const float *values, int count) {
    if (values == NULL) {
        return false;
    }
    for (int i = 0; i < count; ++i) {
        if (!isfinite(values[i])) {
            return false;
        }
    }
    return true;
}

#ifdef AUTONCP_INTEGER_FC
/* Quantize once per layer, then reuse the vector for its four FC branches.
 * Dynamic range avoids imposing an uncalibrated fixed input clipping range.
 * Worst dot bound: MAX_CONCAT * 127 * 127, before restoring float bias/scale. */
_Static_assert((int64_t)AUTONCP_MAX_CONCAT * 127 * 127 <= INT32_MAX,
               "Integer FC accumulator bound exceeds int32");
static bool quantize_fc_input(const float *input, int count) {
    if (count < 1 || count > AUTONCP_MAX_CONCAT || !all_finite(input, count)) return false;
    float maximum = 0.0f;
    for (int i = 0; i < count; ++i)
        maximum = fmaxf(maximum, fabsf(input[i]));
    integer_input_scale = maximum == 0.0f ? 1.0f : fmaxf(maximum / 127.0f, FLT_MIN);
    const float inverse_scale = 1.0f / integer_input_scale;
    for (int i = 0; i < count; ++i) {
        float value = input[i] * inverse_scale;
        int rounded = (int)(value + copysignf(0.5f, value));
        if (rounded > 127) rounded = 127;
        if (rounded < -127) rounded = -127;
        integer_input[i] = (int8_t)rounded;
    }
#ifdef AUTONCP_INTEGER_SIMD
    memset(integer_input + count, 0, ((count + 15) & ~15) - count);
#endif
    return true;
}
#endif

static void fully_connected(const float *input, const autoncp_weight_t *weight,
                            const float *scale, const float *bias,
                            int inputs, int units, float *output) {
    const int stride = AUTONCP_PADDED_ROWS ? (inputs + 15) & ~15 : inputs;
    int row = 0;
#if AUTONCP_BLOCKED_FC && !defined(AUTONCP_INTEGER_FC)
    /* Reuse each activation across four rows, preserving each dot's order. */
    for (; row + 3 < units; row += 4) {
        const autoncp_weight_t *w0 = weight + (size_t)row * stride;
        const autoncp_weight_t *w1 = w0 + stride, *w2 = w1 + stride, *w3 = w2 + stride;
        float d0 = 0, d1 = 0, d2 = 0, d3 = 0;
        for (int column = 0; column < inputs; ++column) {
            const float value = input[column];
            d0 += value * (float)w0[column];
            d1 += value * (float)w1[column];
            d2 += value * (float)w2[column];
            d3 += value * (float)w3[column];
        }
        output[row] = bias[row] + d0 * scale[row];
        output[row + 1] = bias[row + 1] + d1 * scale[row + 1];
        output[row + 2] = bias[row + 2] + d2 * scale[row + 2];
        output[row + 3] = bias[row + 3] + d3 * scale[row + 3];
    }
#endif
    for (; row < units; ++row) {
        const autoncp_weight_t *weights = weight + (size_t)row * stride;
#ifdef AUTONCP_INTEGER_FC
        (void)input;
#ifdef AUTONCP_INTEGER_SIMD
        /* Stage one row in DRAM for PIE loads; timing includes the copy.
         * Padded exports already contain zero tails, avoiding a second write. */
        const int padded = (inputs + 15) & ~15;
        memcpy(integer_weights, weights, AUTONCP_PADDED_ROWS ? padded : inputs);
        if (!AUTONCP_PADDED_ROWS) memset(integer_weights + inputs, 0, padded - inputs);
        int32_t dot = integer_dot(integer_input, integer_weights, padded);
#else
        int32_t dot = 0;
        for (int column = 0; column < inputs; ++column)
            dot += (int32_t)integer_input[column] * (int32_t)weights[column];
#endif
        output[row] = bias[row] + ((float)dot * integer_input_scale) * scale[row];
#else
        float dot = 0.0f;
        for (int column = 0; column < inputs; ++column) {
            dot += input[column] * (float)weights[column];
        }
        output[row] = bias[row] + dot * scale[row];
#endif
    }
}

#if AUTONCP_FUSED_GATE_BITS
/* Fixed ncps ts=1: the export folds both dequantized INT8 gate branches into
 * one INT16 matrix. Keep activations and the accumulator in FP32. */
static void fused_gate(const float *input, const int16_t *weight,
                       const float *scale, const float *bias,
                       int inputs, int units, float *output) {
    const int stride = AUTONCP_PADDED_ROWS ? (inputs + 15) & ~15 : inputs;
    for (int row = 0; row < units; ++row) {
        float dot = 0;
        const int16_t *weights = weight + (size_t)row * stride;
        for (int column = 0; column < inputs; ++column)
            dot += input[column] * (float)weights[column];
        output[row] = bias[row] + dot * scale[row];
    }
}
#endif

static float sigmoid(float value) {
    if (value >= 0.0f) {
        return 1.0f / (1.0f + expf(-value));
    }
    const float exp_value = expf(value);
    return exp_value / (1.0f + exp_value);
}

void autoncp_reset(void) {
    memset(model_state, 0, sizeof(model_state));
}

const float *autoncp_state(void) {
    return model_state;
}

bool autoncp_step(const float normalized_input[AUTONCP_INPUT_SIZE],
                  float normalized_output[AUTONCP_OUTPUT_SIZE]) {
#ifdef AUTONCP_PROFILE
    if (profile_enabled) memset(&profile, 0, sizeof(profile));
#endif
    PROFILE_START(step_started);
    if (normalized_output == NULL ||
        !all_finite(normalized_input, AUTONCP_INPUT_SIZE)) {
        return false;
    }

    const float *layer_input = normalized_input;
    int layer_input_size = AUTONCP_INPUT_SIZE;

    for (int layer_index = 0; layer_index < AUTONCP_LAYER_COUNT; ++layer_index) {
        const autoncp_layer_t *layer = &model_layers[layer_index];
        if (layer->inputs != layer_input_size || layer->units > AUTONCP_MAX_UNITS ||
            layer->state_offset < 0 ||
            layer->state_offset + layer->units > AUTONCP_STATE_SIZE ||
            layer->inputs + layer->units > AUTONCP_MAX_CONCAT) {
            return false;
        }
        float *layer_output = next_state + layer->state_offset;
        for (int i = 0; i < layer->inputs; ++i) {
            concat[i] = layer_input[i];
        }
        for (int i = 0; i < layer->units; ++i) {
            concat[layer->inputs + i] = model_state[layer->state_offset + i];
        }

#ifdef AUTONCP_INTEGER_FC
        PROFILE_START(quantize_started);
        if (!quantize_fc_input(concat, layer->inputs + layer->units)) return false;
        PROFILE_END(profile.quantize_us[layer_index], quantize_started);
#endif

        PROFILE_START(ff1_started);
        fully_connected(concat, layer->weight[AUTONCP_FF1],
                        layer->scale[AUTONCP_FF1], layer->bias[AUTONCP_FF1],
                        layer->inputs + layer->units, layer->units, ff1);
        PROFILE_END(profile.fc_us[layer_index][AUTONCP_FF1], ff1_started);
        PROFILE_START(ff2_started);
        fully_connected(concat, layer->weight[AUTONCP_FF2],
                        layer->scale[AUTONCP_FF2], layer->bias[AUTONCP_FF2],
                        layer->inputs + layer->units, layer->units, ff2);
        PROFILE_END(profile.fc_us[layer_index][AUTONCP_FF2], ff2_started);
        PROFILE_START(time_a_started);
#if AUTONCP_FUSED_GATE_BITS
        fused_gate(concat, layer->weight[AUTONCP_TIME_A],
#else
        fully_connected(concat, layer->weight[AUTONCP_TIME_A],
#endif
                        layer->scale[AUTONCP_TIME_A], layer->bias[AUTONCP_TIME_A],
                        layer->inputs + layer->units, layer->units, time_a);
        PROFILE_END(profile.fc_us[layer_index][AUTONCP_TIME_A], time_a_started);
#if !AUTONCP_FUSED_GATE_BITS
        PROFILE_START(time_b_started);
        fully_connected(concat, layer->weight[AUTONCP_TIME_B],
                        layer->scale[AUTONCP_TIME_B], layer->bias[AUTONCP_TIME_B],
                        layer->inputs + layer->units, layer->units, time_b);
        PROFILE_END(profile.fc_us[layer_index][AUTONCP_TIME_B], time_b_started);
#endif

        if (!all_finite(ff1, layer->units) || !all_finite(ff2, layer->units) ||
            !all_finite(time_a, layer->units)
#if !AUTONCP_FUSED_GATE_BITS
            || !all_finite(time_b, layer->units)
#endif
            ) {
            return false;
        }

        PROFILE_START(activation_started);
        for (int i = 0; i < layer->units; ++i) {
            const float gate = sigmoid(time_a[i]
#if !AUTONCP_FUSED_GATE_BITS
                                       + time_b[i]
#endif
                                       ); /* ncps ts=1 */
            layer_output[i] = tanhf(ff1[i]) * (1.0f - gate) +
                              gate * tanhf(ff2[i]);
        }
        PROFILE_END(profile.activation_us[layer_index], activation_started);
        if (!all_finite(layer_output, layer->units)) return false;
        layer_input = layer_output;
        layer_input_size = layer->units;
    }

#ifdef AUTONCP_INTEGER_FC
    PROFILE_START(head_quantize_started);
    if (!quantize_fc_input(layer_input, layer_input_size)) return false;
    PROFILE_END(profile.quantize_us[AUTONCP_LAYER_COUNT], head_quantize_started);
#endif
    PROFILE_START(head_started);
    fully_connected(layer_input, model_head_weight, model_head_scale,
                    model_head_bias, layer_input_size, AUTONCP_OUTPUT_SIZE,
                    normalized_output);
    PROFILE_END(profile.head_us, head_started);
    if (!all_finite(normalized_output, AUTONCP_OUTPUT_SIZE)) return false;
    memcpy(model_state, next_state, sizeof(model_state));
    PROFILE_END(profile.total_us, step_started);
    return true;
}

bool autoncp_normalize_features(
    const float model_unit_features[AUTONCP_INPUT_SIZE],
    float normalized[AUTONCP_INPUT_SIZE]) {
    if (normalized == NULL || !all_finite(model_unit_features, AUTONCP_INPUT_SIZE) ||
        !isfinite(model_feature_mu) || model_feature_mu <= 0.0f) {
        return false;
    }
    const float log_mu = log1pf(model_feature_mu);
    for (int i = 0; i < AUTONCP_INPUT_SIZE; ++i) {
        if (!isfinite(model_feature_center[i]) || !isfinite(model_feature_scale[i]) ||
            model_feature_scale[i] <= 0.0f) {
            return false;
        }
    }
    for (int i = 0; i < AUTONCP_INPUT_SIZE; ++i) {
        const float scaled = (model_unit_features[i] - model_feature_center[i]) /
                             model_feature_scale[i];
        normalized[i] = copysignf(log1pf(model_feature_mu * fabsf(scaled)) / log_mu,
                                  scaled);
    }
    return all_finite(normalized, AUTONCP_INPUT_SIZE);
}

bool autoncp_inverse_targets(const float normalized[AUTONCP_OUTPUT_SIZE],
                             float values[AUTONCP_OUTPUT_SIZE]) {
    if (values == NULL || !all_finite(normalized, AUTONCP_OUTPUT_SIZE) ||
        !isfinite(model_target_mu) || model_target_mu <= 0.0f) {
        return false;
    }
    const float log_mu = log1pf(model_target_mu);
    for (int i = 0; i < AUTONCP_OUTPUT_SIZE; ++i) {
        if (!isfinite(model_target_center[i]) || !isfinite(model_target_scale[i]) ||
            model_target_scale[i] <= 0.0f) {
            return false;
        }
    }
    for (int i = 0; i < AUTONCP_OUTPUT_SIZE; ++i) {
        const float expanded = copysignf(expm1f(fabsf(normalized[i]) * log_mu) /
                                          model_target_mu, normalized[i]);
        values[i] = expanded * model_target_scale[i] + model_target_center[i];
    }
    return all_finite(values, AUTONCP_OUTPUT_SIZE);
}
