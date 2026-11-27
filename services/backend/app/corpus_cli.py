import argparse,hashlib,json,os,re,subprocess,sys,tempfile
from pathlib import Path
from uuid import UUID, uuid4
from psycopg.types.json import Jsonb
from app.database import connect
from app.config import ROOT
from app.storage.r2 import S3StorageGateway
from app.media.subtitles import parse_srt,align,normalize


def probe(g,bucket,key):
    import imageio_ffmpeg
    p=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-nostdin','-i',g.sign_read(bucket,key,600)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
    info=p.stderr
    match=re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)',info)
    if not match:raise ValueError('remote_probe_failed')
    h,m,s=map(float,match.groups());duration=round((h*3600+m*60+s)*1000)
    return duration, bool(re.search(r'Video: h264',info) and re.search(r'Audio: aac',info)),imageio_ffmpeg.get_ffmpeg_exe()


def run_import(version,offset=0,scale=1,corrections_path=None):
    if scale<=0 or scale>2:raise ValueError('invalid_time_scale')
    g=S3StorageGateway()
    corrections=json.loads(Path(corrections_path).read_text(encoding='utf-8-sig')) if corrections_path else []
    if not isinstance(corrections,list):raise ValueError('corrections_must_be_list')
    with connect() as conn:
        video=conn.execute("""SELECT a.bucket,a.object_key,a.size_bytes,a.sha256 FROM dialogue.video_versions v JOIN dialogue.assets a ON a.id=v.source_asset_id WHERE v.id=%s""",(version,)).fetchone()
        tracks=conn.execute("""SELECT t.id,t.language,a.bucket,a.object_key,a.sha256 FROM dialogue.subtitle_tracks t JOIN dialogue.assets a ON a.id=t.source_asset_id WHERE t.version_id=%s ORDER BY t.language,t.revision DESC""",(version,)).fetchall()
    if not video:raise ValueError('unknown_version')
    chosen={}
    for t in tracks:chosen.setdefault(t[1],t)
    if set(chosen)!={'en','zh'}:raise ValueError('both_tracks_required')
    identity=json.dumps([str(version),[(lang,chosen[lang][4]) for lang in sorted(chosen)],offset,scale,corrections,'align-v1'])
    dedupe=hashlib.sha256(identity.encode()).hexdigest()
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(str(version),))
        existing=conn.execute("SELECT payload FROM dialogue.jobs WHERE dedupe_key=%s AND status='ready'",(dedupe,)).fetchone()
        if existing:
            print(json.dumps({'status':'already_imported',**existing[0]},ensure_ascii=False));return
        duration,compatible,exe=probe(g,*video[:2])
        proxy_asset=None
        if not compatible:
            quota=int(os.getenv('TEMP_QUOTA_GB','2'))*1024**3
            if video[2]*3>quota:raise ValueError('temporary_quota_insufficient')
            # Bound source plus output; never retain full episode cache.
            with tempfile.TemporaryDirectory(prefix='corpus-',dir=ROOT/'data/tmp') as temp:
                source=Path(temp)/'input';out=Path(temp)/'proxy.mp4'
                g.client.download_file(video[0],video[1],str(source))
                process=subprocess.Popen([exe,'-nostdin','-v','error','-i',str(source),'-vf','scale=trunc(iw/2)*2:trunc(ih/2)*2','-c:v','libx264','-threads','1','-crf','23','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(out)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                import time
                deadline=time.monotonic()+3600
                while process.poll() is None:
                    if sum(x.stat().st_size for x in Path(temp).iterdir())>quota or time.monotonic()>deadline:
                        process.kill();process.wait();raise ValueError('temporary_quota_or_timeout')
                    time.sleep(0.5)
                if process.returncode:raise ValueError('proxy_transcode_failed')
                key=f'derived/{version}/proxy-v1.mp4';bucket=os.environ['S3_DERIVED_BUCKET']
                sha,size=g.upload(out,bucket,key,'video/mp4',ROOT/'data/tmp/r2-resume')
                from app.storage_cli import register_asset
                proxy_asset=register_asset(conn,bucket,key,sha,size,'video/mp4','proxy')
        else:
            proxy_asset=conn.execute('SELECT source_asset_id FROM dialogue.video_versions WHERE id=%s',(version,)).fetchone()[0]
        cues={}
        for lang,t in chosen.items():
            obj=g.client.get_object(Bucket=t[2],Key=t[3]);body=obj['Body']
            try:
                raw=body.read(5*1024*1024+1)
            finally:body.close()
            if len(raw)>5*1024*1024:raise ValueError('subtitle_too_large')
            if hashlib.sha256(raw).hexdigest()!=t[4]:raise ValueError('subtitle_hash_mismatch')
            cues[lang]=parse_srt(raw)
        import copy
        originals=copy.deepcopy(cues)
        for edit in corrections:
            if edit.get('language') not in cues or set(edit)-{'language','seq','text','start_ms','end_ms'}:raise ValueError('invalid_correction')
            matches=[c for c in cues[edit['language']] if c['seq']==edit.get('seq')]
            if len(matches)!=1:raise ValueError('unknown_cue')
            c=matches[0]
            if 'text' in edit:
                if not isinstance(edit['text'],str) or not edit['text'].strip():raise ValueError('empty_correction_text')
                c['text']=edit['text']
            for field,target in [('start_ms','start'),('end_ms','end')]:
                if field in edit:
                    if not isinstance(edit[field],int):raise ValueError('invalid_correction_time')
                    c[target]=edit[field]
        segments=align(cues,duration,offset,scale)
        rev=conn.execute('SELECT coalesce(max(revision),0)+1 FROM dialogue.subtitle_revisions WHERE version_id=%s',(version,)).fetchone()[0]
        revision=conn.execute("INSERT INTO dialogue.subtitle_revisions(version_id,revision,status) VALUES (%s,%s,'needs_review') RETURNING id",(version,rev)).fetchone()[0]
        cue_ids={}
        for lang,items in originals.items():
            track=chosen[lang][0]
            rows=[(track,version,c['seq'],c['raw'],c['text'],c['start'],c['end']) for c in items]
            conn.cursor().executemany("""INSERT INTO dialogue.subtitle_cues(track_id,version_id,seq,raw_text,clean_text,start_ms,end_ms) VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(track_id,seq) DO NOTHING""",rows)
            for cid,seq in conn.execute('SELECT id,seq FROM dialogue.subtitle_cues WHERE track_id=%s',(track,)):
                cue_ids[lang,seq]=cid
        report={'version_id':str(version),'revision_id':str(revision),'cue_counts':{k:len(v) for k,v in cues.items()},'segment_count':len(segments),'offset_ms':offset,'scale':scale,'duration_ms':duration,'proxy_reused':compatible,'sync_verified':False,'corrections':corrections,'issues':[],'sample_ids':[]}
        ids=[];segment_rows=[];links=[]
        for i,s in enumerate(segments,1):
            sid=uuid4();ids.append(str(sid))
            segment_rows.append((sid,revision,version,i,s['start'],s['end'],s['en'],s['zh'],normalize(s['en']),normalize(s['zh'])))
            links.extend((sid,cue_ids[n['language'],n['seq']],version) for n in s['nodes'])
            if s['flags']:report['issues'].append({'segment_id':str(sid),'start_ms':s['start'],'flags':s['flags']})
        conn.cursor().executemany("""INSERT INTO dialogue.segments(id,revision_id,version_id,seq,start_ms,end_ms,text_en,text_zh,search_en,search_zh) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",segment_rows)
        conn.cursor().executemany('INSERT INTO dialogue.segment_cues(segment_id,cue_id,version_id) VALUES (%s,%s,%s)',links)
        report['sample_ids']=[ids[round(i*(len(ids)-1)/19)] for i in range(20)] if len(ids)>=20 else ids
        for lang,items in cues.items():
            overlaps=sum(b['start']<a['end'] for a,b in zip(sorted(items,key=lambda x:x['start']),sorted(items,key=lambda x:x['start'])[1:]))
            report[lang+'_overlap_count']=overlaps
        report['drift_note']='Audio synchronization requires human review; no automatic audio drift detection performed.'
        report_key=f'derived/{version}/{revision}/alignment-report.json'
        data=json.dumps(report,ensure_ascii=False,indent=2).encode()
        g.client.put_object(Bucket=os.environ['S3_DERIVED_BUCKET'],Key=report_key,Body=data,ContentType='application/json')
        report['report_key']=report_key
        conn.execute("INSERT INTO dialogue.jobs(type,payload,status,dedupe_key) VALUES ('import',%s,'ready',%s)",(Jsonb(report),dedupe))
        conn.execute("UPDATE dialogue.video_versions SET duration_ms=%s,proxy_asset_id=%s,status=CASE WHEN status='published' THEN status ELSE 'needs_review' END WHERE id=%s",(duration,proxy_asset,version))
    (ROOT/'data/processed'/f'{version}.alignment-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'needs_review','revision_id':str(revision),'segments':len(segments),'issues':len(report['issues']),'samples':len(report['sample_ids'])}))


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['import','approve']);p.add_argument('--version',type=UUID,required=True);p.add_argument('--offset-ms',type=int,default=0);p.add_argument('--scale',type=float,default=1);p.add_argument('--corrections',help='JSON cue correction list; creates a new subtitle revision');p.add_argument('--revision',type=UUID);p.add_argument('--confirm-reviewed',action='store_true');args=p.parse_args()
    if args.action=='import':run_import(args.version,args.offset_ms,args.scale,args.corrections);return
    if not args.confirm_reviewed or not args.revision:raise ValueError('explicit_review_confirmation_required')
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(str(args.version),))
        row=conn.execute("SELECT status FROM dialogue.subtitle_revisions WHERE id=%s AND version_id=%s FOR UPDATE",(args.revision,args.version)).fetchone()
        if not row:raise ValueError('unknown_revision')
        # Local review approval is separate from public rights authorization.
        conn.execute("UPDATE dialogue.segments SET review_status='approved' WHERE revision_id=%s",(args.revision,))
        conn.execute("UPDATE dialogue.subtitle_revisions SET status='published',published_at=now() WHERE id=%s",(args.revision,))
        conn.execute("UPDATE dialogue.video_versions SET active_revision_id=%s,status='ready' WHERE id=%s",(args.revision,args.version))
        conn.execute("INSERT INTO dialogue.audit_events(action,target,details) VALUES ('local_review_approved',%s,%s)",(str(args.revision),Jsonb({'public_publish':False})))
    print(json.dumps({'status':'review_approved','public_publish':False}))

if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'status':'failed','error_type':type(e).__name__,'reason':str(e) if isinstance(e,ValueError) else 'see_operator_checks'}));sys.exit(1)
