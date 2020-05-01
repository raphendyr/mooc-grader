#!/bin/sh
set -- "$1" "http://$(hostname -i):8080" "$3" "$4" "$5" "$6" "$7" "$8"
#echo
#for i in `seq $#`; do
#    eval "arg=\${$i}"
#    echo " -- \$$i=$arg"
#done
#echo "-"
exec /local/venv_grader/bin/python3 /srv/grader/scripts/kubernetes-run.py "$@"
