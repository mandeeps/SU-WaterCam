# https://www.uugear.com/forums/technial-support-discussion/witty-pi-4-how-to-synchronise-time-with-internet-on-boot/
#
[ -z $BASH ] && { exec bash "$0" "$@" || exit; }
#!/bin/bash
#
# Script to write network time to system and the RTC
#

# include utilities script in same directory
my_dir="`dirname \"$0\"`"
my_dir="`( cd \"$my_dir\" && pwd )`"
if [ -z "$my_dir" ] ; then
  exit 1
fi
. $my_dir/utilities.sh

# wait long enough so the whole system gets stable before processing
sleep 30

# write network time to system and the RTC
net_to_system
system_to_rtc
