#include "residual.h"

/*
 * residual.c -- ComputeResidual()/ReconstructCode() from pseudocode.txt.
 *
 * A first-order predictor: each sample is coded as its difference from the
 * previous quantizer code. The two functions are exact inverses when fed the
 * same *prev, so encode-then-decode reproduces the original code stream.
 */

int glx_compute_residual(uint8_t code, int *prev)
{
    int residual = (int)code - *prev;
    *prev = (int)code;
    return residual;
}

uint8_t glx_reconstruct_code(int residual, int *prev)
{
    int code = residual + *prev;
    *prev = code;
    return (uint8_t)code;
}
