#!/bin/bash
pkill -f "flux serve studio" || true
pkill -f "jury_evaluator.py" || true
pkill -f "perpetual_feeder.py" || true

nohup /root/.local/bin/flux serve studio > /root/CLIs/flux/.fluxd/studio.log 2>&1 &
echo "Started Studio PID: $!"

sleep 2

nohup /usr/bin/python3 -u /root/CLIs/flux/jury_evaluator.py >> /root/CLIs/flux/.fluxd/jury_evaluator.log 2>&1 &
echo "Started Evaluator PID: $!"

nohup /usr/bin/python3 -u /root/CLIs/flux/perpetual_feeder.py >> /root/CLIs/flux/.fluxd/feeder.log 2>&1 &
echo "Started Feeder PID: $!"
