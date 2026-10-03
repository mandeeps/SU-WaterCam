#!/bin/bash
# file: beforeScript.sh
#
# This script will run after Raspberry Pi boot up, and before running the schedule script.
# If you want to change the schedule script before running it, you may do so here.
#
# Remarks: please use absolute path of the command, or it can not be found (by root user).
# Remarks: you may append '&' at the end of command to avoid blocking the main daemon.sh.
#
# The schedule script (which arms the next startup alarm) only runs once this
# script returns, and syncTime.sh sleeps 30 s and then waits for network time.
# Run synchronously, that left the next wake unarmed for ~55 s of the boot's
# heaviest load; a brownout in that window strands the unit until someone
# presses the button. So sync in the background when the RTC had good time at
# boot, and only block when it did not.
#
# rtc_has_bad_time can't tell us that here: when the RTC is bad, daemon.sh has
# already copied the (possibly stale) system time into it. Its verdict for this
# boot is the last RTC line it logged, just before running this script.
rtc_verdict=$(tail -c 8192 /home/pi/wittypi/wittyPi.log | grep -aoE 'RTC has bad time|RTC has good time' | tail -1)
if [ "$rtc_verdict" == "RTC has bad time" ]; then
  /home/pi/wittypi/syncTime.sh
else
  /home/pi/wittypi/syncTime.sh &
fi
