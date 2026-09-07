#!/usr/bin/env python3
"""Hydrate declared R2 models, then launch one assigned FLUX Beauty worker."""
import argparse, json, os, pathlib, subprocess, sys
manifest = pathlib.Path(os.environ.get('AONS_MODEL_MANIFEST', '/etc/aons/models.manifest.json'))
def ready(model):
    p=pathlib.Path(model['path']); return all((p / x).exists() for x in model['sentinels'])
def hydrate(model):
    if ready(model): return
    remote=os.environ.get('R2_REMOTE')
    if not remote: raise SystemExit(f"{model['id']} missing; set R2_REMOTE for rclone hydration")
    p=pathlib.Path(model['path']); p.mkdir(parents=True, exist_ok=True)
    subprocess.run(['rclone','copy',f"{remote}:{model['r2_prefix']}",str(p),'--checksum','--transfers','8'],check=True)
    if not ready(model): raise SystemExit(f"hydrate incomplete for {model['id']}")
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--hydrate-only',action='store_true'); ap.add_argument('--roles',default='generator'); ap.add_argument('--socket',default='/state/flux.sock'); ap.add_argument('--state',default='/state/flux.json'); ap.add_argument('--out-dir',default='/outputs/collections/flux-beauty-study'); args=ap.parse_args()
    data=json.loads(manifest.read_text()); roles=set(args.roles.split(','))
    for m in data['models']:
        if m['role'] in roles: hydrate(m)
    if args.hydrate_only: return
    model=next(m for m in data['models'] if m['role']=='generator')
    env=os.environ.copy(); env['MODEL_DIR']=model['path']; env['OUT_DIR']=args.out_dir
    pathlib.Path(args.out_dir).mkdir(parents=True,exist_ok=True); pathlib.Path(args.state).parent.mkdir(parents=True,exist_ok=True)
    os.execvpe('python3',['python3','/app/worker.py','--socket',args.socket,'--state',args.state,'--model-dir',model['path'],'--out-dir',args.out_dir,'--backend','cuda','--preload'],env)
if __name__=='__main__': main()
