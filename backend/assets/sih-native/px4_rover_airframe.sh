#!/bin/sh
# Source after PX4's version-specific 4009_gz_r1_rover airframe, preserving its
# rover controller defaults and replacing only the simulator/actuator mapping.
param set SIM_GZ_EN 0
param set SENS_EN_GPSSIM 0
param set SENS_EN_BAROSIM 0
param set SENS_EN_MAGSIM 0
param set SENS_EN_ARSPDSIM 0
param set CA_AIRFRAME 6
param set CA_R_REV 3
param set PWM_MAIN_FUNC1 101
param set PWM_MAIN_FUNC2 102
param set PWM_MAIN_FUNC3 0
param set PWM_MAIN_FUNC4 0
param set PWM_MAIN_FUNC5 0
param set PWM_MAIN_FUNC6 0
param set PWM_MAIN_FUNC7 0
param set PWM_MAIN_FUNC8 0
param set PWM_MAIN_FUNC9 0
param set PWM_MAIN_FUNC10 0
param set PWM_MAIN_FUNC11 0
param set PWM_MAIN_FUNC12 0
param set PWM_MAIN_FUNC13 0
param set PWM_MAIN_FUNC14 0
param set PWM_MAIN_FUNC15 0
param set PWM_MAIN_FUNC16 0
param set PWM_MAIN_REV 0
param set EKF2_MAG_TYPE 1
param set-default EKF2_GPS_DELAY 0
