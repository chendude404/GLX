#ifndef GLX_RESIDUAL_H
#define GLX_RESIDUAL_H

#include <stdint.h>

/*
 * residual.h -- first-order (delta) residual, per pseudocode.txt
 * ComputeResidual()/ReconstructCode().
 *
 *   ComputeResidual:  residual <- code - previous;  previous <- code
 *   ReconstructCode:  code     <- residual + previous; previous <- code
 *
 * `previous` is caller-owned and starts at 0 (as in the pseudocode), carried
 * across every sample of the stream.
 */

/* code -> residual, advancing the predictor *prev. */
int glx_compute_residual(uint8_t code, int *prev);

/* residual -> code, advancing the predictor *prev. */
uint8_t glx_reconstruct_code(int residual, int *prev);

#endif /* GLX_RESIDUAL_H */
