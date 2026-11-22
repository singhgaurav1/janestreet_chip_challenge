# Protocol drivers on the Protocol Engines. An app lists them in
# <app>_DRIVERS; each brings its C sources and the engine programs it loads
# (assembled at build time; the programs are the spec's examples).
DRV_uart_SRC := drivers/pe_uart.c
DRV_uart_PE  := ../examples/pe/uart.pe
