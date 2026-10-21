#!/bin/sh
# The selected 4009 airframe supplies version-specific rover controller tuning.
# Use the MAVLink simulator and normalized reversible motor outputs instead of gz.
param set SIM_GZ_EN 0
param set SENS_EN_GPSSIM 0
param set SENS_EN_BAROSIM 0
param set SENS_EN_MAGSIM 0
param set SENS_EN_ARSPDSIM 0
param set MAV_TYPE 10
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
param set-default EKF2_GPS_DELAY 0
