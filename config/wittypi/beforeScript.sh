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
# presses the button. So sync in the background whenever the RTC already has a
# time to schedule from, and only block when it does not.
. /home/pi/wittypi/utilities.sh
if [ "$(rtc_has_bad_time)" == "1" ]; then
  /home/pi/wittypi/syncTime.sh
else
  /home/pi/wittypi/syncTime.sh &
fi
