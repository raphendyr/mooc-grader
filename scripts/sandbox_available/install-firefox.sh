#!/bin/bash
#
# Provides firefox browser for in browser testing.
# Tests are recommended to run with python using selenium.webdriver.Firefox()
# The selenium can be installed with pip inside a virtualenv.
#
if ! grep --quiet universe /etc/apt/sources.list
then
	echo "deb http://archive.ubuntu.com/ubuntu precise universe restricted" >> /etc/apt/sources.list
	apt-get -q update
fi
if ! grep --quiet precise-security /etc/apt/sources.list
then
	echo "deb http://archive.ubuntu.com/ubuntu precise-security main universe restricted" >> /etc/apt/sources.list
	apt-get -q update
fi
apt-get -qy install firefox
