# UART on the engines, checked by an independent UART model (cocotbext-uart)
# in test/fw/cocotb. One image per baud rate.
uart_TARGETS := chip
uart_DRIVERS := uart
uart_VARIANTS := 115200 1000000 3000000
