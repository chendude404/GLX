# Makefile -- build the GLX encoder and decoder.
#
#   make            build glx_encode and glx_decode
#   make tables     regenerate the lookup tables in src/generated/
#   make clean      remove binaries and object files
#
# Layout:
#   src/            codec library + the two CLI entry points
#   src/generated/  tables baked by tools/ -- do not edit by hand
#   tools/          Python table generators (the only floating-point code here)
#
# The C binaries are libm-free; only the Python generators use math.

CC       ?= cc
CFLAGS   ?= -O2 -Wall -Wextra -std=c11
CPPFLAGS ?= -Isrc -Isrc/generated
PYTHON   ?= python3

SRCDIR = src
GENDIR = $(SRCDIR)/generated
TOOLS  = tools

# The two translation units carrying main(); everything else in src/ is library.
MAINS      = $(SRCDIR)/encoder.c $(SRCDIR)/decoder.c
COMPONENTS = $(filter-out $(MAINS),$(wildcard $(SRCDIR)/*.c))

# Generated headers the components depend on.
GENERATED = $(GENDIR)/compression_lut.h $(GENDIR)/resample_taps.h \
            $(GENDIR)/huffman_lut.h     $(GENDIR)/crc_lut.h

all: glx_encode glx_decode

glx_encode: $(SRCDIR)/encoder.c $(COMPONENTS) $(GENERATED)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ $(SRCDIR)/encoder.c $(COMPONENTS)

glx_decode: $(SRCDIR)/decoder.c $(COMPONENTS) $(GENERATED)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ $(SRCDIR)/decoder.c $(COMPONENTS)

# Regenerate the fixed lookup tables from their sources. Each generator writes
# into src/generated/ relative to its own location.
tables: $(GENERATED)

$(GENDIR)/compression_lut.h: $(TOOLS)/gen_compression_lut.py | $(GENDIR)
	$(PYTHON) $<

$(GENDIR)/crc_lut.h: $(TOOLS)/gen_crc_lut.py | $(GENDIR)
	$(PYTHON) $<

$(GENDIR)/resample_taps.h: $(TOOLS)/gen_resample_taps.py | $(GENDIR)
	$(PYTHON) $<

$(GENDIR)/huffman_lut.h: $(TOOLS)/gen_huffman_lut.py $(TOOLS)/huffman_tables_10.csv | $(GENDIR)
	$(PYTHON) $<

# Order-only: the directory must exist, but its mtime must not trigger rebuilds.
$(GENDIR):
	mkdir -p $@

clean:
	rm -f glx_encode glx_decode glx_encode.exe glx_decode.exe $(SRCDIR)/*.o

.PHONY: all tables clean
