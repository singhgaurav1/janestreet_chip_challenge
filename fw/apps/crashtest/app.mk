# One image per failure mode: crashtest-<variant>, built with -DVARIANT=<n>.
crashtest_VARIANTS := illegal loadfault fetchfault ecall nmi stackovf assert exitcode spin pinpanic beyondram
crashtest_CFLAGS := -DV_illegal=1 -DV_loadfault=2 -DV_fetchfault=3 -DV_ecall=4 -DV_nmi=5 \
                    -DV_stackovf=6 -DV_assert=7 -DV_exitcode=8 -DV_spin=9 -DV_pinpanic=10 -DV_beyondram=11 \
                    -Dillegal=V_illegal -Dloadfault=V_loadfault -Dfetchfault=V_fetchfault \
                    -Decall=V_ecall -Dnmi=V_nmi -Dstackovf=V_stackovf -Dassert=V_assert \
                    -Dexitcode=V_exitcode -Dspin=V_spin -Dpinpanic=V_pinpanic -Dbeyondram=V_beyondram
