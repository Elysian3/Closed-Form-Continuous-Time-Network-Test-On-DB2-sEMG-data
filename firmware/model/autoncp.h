#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "autoncp_dims.h"

/* Old exports retain their original row layout and separate gate branches. */
#ifndef AUTONCP_PADDED_ROWS
#define AUTONCP_PADDED_ROWS 0
#endif
#ifndef AUTONCP_FUSED_GATE_BITS
#define AUTONCP_FUSED_GATE_BITS 0
#endif
#ifndef AUTONCP_BLOCKED_FC
#define AUTONCP_BLOCKED_FC 0
#endif
#if AUTONCP_FUSED_GATE_BITS != 0 && AUTONCP_FUSED_GATE_BITS != 16
#error "Only the fixed-ts INT16 fused gate is supported"
#endif
#if AUTONCP_FUSED_GATE_BITS && defined(AUTONCP_INTEGER_FC)
#error "The fused gate requires FP32 activations"
#endif

#if AUTONCP_WEIGHT_BITS == 8
typedef int8_t autoncp_weight_t;
#elif AUTONCP_WEIGHT_BITS == 16
typedef int16_t autoncp_weight_t;
#else
#error "AUTONCP_WEIGHT_BITS must be 8 or 16"
#endif

/*
 * This runtime has one persistent state instance.  Its caller must serialize
 * access and call autoncp_reset() at discontinuities in the input stream.
 */
void autoncp_reset(void);

/* Advance one normalized RMS/ZC feature frame and return normalized targets. */
bool autoncp_step(const float normalized_input[AUTONCP_INPUT_SIZE],
                  float normalized_output[AUTONCP_OUTPUT_SIZE]);

/* The returned pointer remains valid until the next reset or successful step. */
const float *autoncp_state(void);

/* Apply the checkpoint's mu-law feature transform to model-unit features. */
bool autoncp_normalize_features(
    const float model_unit_features[AUTONCP_INPUT_SIZE],
    float normalized[AUTONCP_INPUT_SIZE]);

/* Map normalized model outputs back through the checkpoint's mu-law transform. */
bool autoncp_inverse_targets(const float normalized[AUTONCP_OUTPUT_SIZE],
                             float values[AUTONCP_OUTPUT_SIZE]);

#ifdef AUTONCP_PROFILE
/* Test builds only; timestamps are supplied by the benchmark caller. */
typedef struct {
    int64_t fc_us[AUTONCP_LAYER_COUNT][4];
    int64_t activation_us[AUTONCP_LAYER_COUNT];
    int64_t head_us;
    int64_t total_us;
    int64_t quantize_us[AUTONCP_LAYER_COUNT + 1];
} autoncp_profile_t;
int64_t autoncp_profile_now_us(void);
void autoncp_profile_enable(bool enabled);
const autoncp_profile_t *autoncp_profile_last(void);
#endif

#ifdef AUTONCP_BENCHMARK
const void *autoncp_benchmark_weight_address(void);
#ifdef AUTONCP_INTEGER_SIMD
bool autoncp_integer_dot_self_test(void);
#endif
#endif
