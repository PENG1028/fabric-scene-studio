"""Split a calibrated five-column transparency chart without modifying its source."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable'))
from transparency import LEVELS, load_crop


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('chart')
    parser.add_argument('--output',required=True)
    parser.add_argument('--region',type=float,nargs=4,required=True,metavar=('LEFT','TOP','RIGHT','BOTTOM'))
    args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    source=Path(args.chart).resolve()
    entries=[]
    for level,(label,_) in LEVELS.items():
        crop,box=load_crop(source,level,args.region)
        target=output/f'level-{level}.png'
        if target.resolve()==source:raise ValueError('Do not overwrite source')
        crop.save(target)
        entries.append(dict(level=level,label=label,file=target.name,box=box))
    (output/'manifest.json').write_text(json.dumps(dict(source=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),region=args.region,crops=entries),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output.resolve()),count=5,api_calls=0),ensure_ascii=False))


if __name__=='__main__':main()
