# Makefile -- build the GLX encoder and decoder.
#
#   make            build glx_encode and glx_decode
#   make tables     regenerate compression_lut.h, resample_taps.h, huffman_lut.h
#   make clean      remove binaries and object files
#
# The C binaries are libm-free; only the Python generators use math.

CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra -std=c11
PYTHON  ?= python3

# Shared codec components (one translation unit per pipeline stage).
COMPONENTS = resample.c compression.c dither.c quantizer.c residual.c bitstream.c huffman.c crc.c

# Generated headers the components depend on.
GENERATED  = compression_lut.h resample_taps.h huffman_lut.h crc_lut.h

all: glx_encode glx_decode

glx_encode: encoder.c $(COMPONENTS) $(GENERATED)
	$(CC) $(CFLAGS) -o $@ encoder.c $(COMPONENTS)

glx_decode: decoder.c $(COMPONENTS) $(GENERATED)
	$(CC) $(CFLAGS) -o $@ decoder.c $(COMPONENTS)

# Regenerate the fixed lookup tables from their sources.
tables: compression_lut.h resample_taps.h huffman_lut.h crc_lut.h

compression_lut.h: gen_compression_lut.py
	$(PYTHON) gen_compression_lut.py

crc_lut.h: gen_crc_lut.py
	$(PYTHON) gen_crc_lut.py

resample_taps.h: gen_resample_taps.py
	$(PYTHON) gen_resample_taps.py

huffman_lut.h: gen_huffman_lut.py huffman_tables_10.csv
	$(PYTHON) gen_huffman_lut.py

clean:
	rm -f glx_encode glx_decode *.o

.PHONY: all tables clean
