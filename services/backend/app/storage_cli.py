"""Local-only M3 operator CLI. Never expose these administrative actions anonymously."""
import argparse,json,os,sys
from pathlib import Path
import httpx
from botocore.exceptions import ClientError,BotoCoreError
from app.config import ROOT
from app.database import connect
from app.storage.r2 import S3StorageGateway,digest


def register_asset(conn,bucket,key,sha,size,mime,kind):
    row=conn.execute("""INSERT INTO dialogue.assets(bucket,object_key,sha256,size_bytes,mime,kind,status)
      VALUES (%s,%s,%s,%s,%s,%s,'ready') ON CONFLICT(bucket,object_key) DO UPDATE
      SET status='ready' WHERE dialogue.assets.sha256=EXCLUDED.sha256 AND dialogue.assets.size_bytes=EXCLUDED.size_bytes
      RETURNING id""",(bucket,key,sha,size,mime,kind)).fetchone()
    if not row:raise ValueError('asset_conflict')
    return row[0]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['check','cors-check','upload-episode'])
    parser.add_argument('--stop-after',type=int,help='Pause after N new video parts; rerun without this option to resume')
    parser.add_argument('--folder',default=str(ROOT/'data/processed'),help='Explicit prepared source folder')
    parser.add_argument('--full-verify',action='store_true',help='Stream the whole remote video for end-to-end SHA-256 verification')
    args=parser.parse_args();g=S3StorageGateway()
    if args.action=='check':
        print(json.dumps(g.health()));return
    if args.action=='cors-check':
        passed=True
        for bucket in sorted(g.buckets):
            for origin in ('http://localhost:5173','http://127.0.0.1:5173'):
                url=g.sign_read(bucket,'cors-probe').split('?',1)[0]
                response=httpx.options(url,headers={'Origin':origin,'Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'range'},timeout=20)
                ok=response.is_success and response.headers.get('access-control-allow-origin')==origin
                passed=passed and ok
                print(json.dumps({'bucket':bucket,'origin':origin,'cors_ok':ok}))
        if not passed:raise ValueError('cors_configuration_required')
        return
    folder=Path(args.folder)
    files=[(folder/'S01E01.mp4','video/mp4','source_video'),
           (next(folder.glob('*.en.srt')),'application/x-subrip','source_subtitle'),
           (next(folder.glob('*.zh.srt')),'application/x-subrip','source_subtitle')]
    bucket=os.environ['S3_ORIGINALS_BUCKET'];assets=[]
    for path,mime,kind in files:
        sha=digest(path);key=f'the-big-bang-theory/s01/e01/{sha}/{path.name}'
        sha,size=g.upload(path,bucket,key,mime,ROOT/'data/tmp/r2-resume',args.stop_after if kind=='source_video' else None)
        # Stream remote content to hash it; no second full local copy.
        if size < 1024*1024 or args.full_verify:
            g.verify_content(bucket,key,sha)
        signed=g.sign_read(bucket,key)
        with httpx.Client(timeout=30) as client:
            response=client.get(signed,headers={'Range':'bytes=0-15'})
            if response.status_code!=206 or len(response.content)!=16:raise ValueError('signed_range_failed')
            with path.open('rb') as local:
                if response.content != local.read(16):raise ValueError('range_content_mismatch')
                if size > 1024*1024:
                    for offset in (size//2,size-16):
                        local.seek(offset)
                        sample=client.get(signed,headers={'Range':f'bytes={offset}-{offset+15}'})
                        if sample.status_code!=206 or sample.content!=local.read(16):raise ValueError('range_content_mismatch')
            unsigned=signed.split('?',1)[0]
            response=client.head(unsigned)
            if response.status_code in (200,206):raise ValueError('anonymous_access_allowed')
        assets.append((bucket,key,sha,size,mime,kind))
        print(json.dumps({'event':'verified','file':path.name,'bytes':size}),flush=True)
    with connect() as conn:
        series=conn.execute("""INSERT INTO dialogue.series(slug,title_zh,title_en) VALUES ('the-big-bang-theory','The Big Bang Theory','The Big Bang Theory')
         ON CONFLICT(slug) DO UPDATE SET slug=EXCLUDED.slug RETURNING id""").fetchone()[0]
        episode=conn.execute("""INSERT INTO dialogue.episodes(series_id,season,episode_number) VALUES (%s,1,1)
         ON CONFLICT(series_id,season,episode_number) DO UPDATE SET season=EXCLUDED.season RETURNING id""",(series,)).fetchone()[0]
        ids=[register_asset(conn,*a) for a in assets]
        edition='local-mkv-'+assets[0][2][:12]
        version=conn.execute("""INSERT INTO dialogue.video_versions(episode_id,edition,source_asset_id,status)
         VALUES (%s,%s,%s,'uploaded') ON CONFLICT(episode_id,edition) DO UPDATE SET edition=EXCLUDED.edition RETURNING id""",(episode,edition,ids[0])).fetchone()[0]
        conn.execute("INSERT INTO dialogue.rights(version_id,evidence_ref) VALUES (%s,'local-development-only; public rights unverified') ON CONFLICT(version_id) DO NOTHING",(version,))
        for language,asset in zip(['en','zh'],ids[1:]):
            conn.execute("""INSERT INTO dialogue.subtitle_tracks(version_id,language,source_asset_id,provenance)
              VALUES (%s,%s,%s,'split from user-provided ASS; synchronization unverified')
              ON CONFLICT(version_id,language,revision) DO NOTHING""",(version,language,asset))
    print(json.dumps({'status':'registered','assets':len(ids),'version_id':str(version),'public_publish':False}))

if __name__=='__main__':
    try: main()
    except InterruptedError:
        print(json.dumps({'status':'paused','resume':'rerun without --stop-after'}));sys.exit(2)
    except (ClientError,BotoCoreError) as e:
        code=e.response['Error']['Code'] if isinstance(e,ClientError) else type(e).__name__
        print(json.dumps({'status':'storage_failed','code':code}));sys.exit(1)
    except Exception as e:
        print(json.dumps({'status':'failed','error_type':type(e).__name__}));sys.exit(1)
