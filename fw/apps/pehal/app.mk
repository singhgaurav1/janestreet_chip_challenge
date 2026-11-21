# Three images (together they do not fit 4 KiB):
#   fifo     reset values, forced instructions, FIFO levels and sticky errors
#   engines  two engines started together, busy program memory
#   pads     pins, open drain, pad 23, then the safe-state panic
pehal_TARGETS := chip
pehal_HAL := 1
pehal_VARIANTS := fifo engines pads
pehal_CFLAGS := -DV_fifo=1 -DV_engines=2 -DV_pads=3 -Dfifo=V_fifo -Dengines=V_engines -Dpads=V_pads
