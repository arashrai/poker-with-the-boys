#!/bin/bash

python3 new_graph_night.py -i $1 &
PID=$!

python3 graph_night.py -i $1

kill -9 $PID
