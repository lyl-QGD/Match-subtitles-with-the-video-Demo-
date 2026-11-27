"""Local development review only. Never enable on a public deployment."""
import os
from uuid import UUID
from fastapi import APIRouter,HTTPException,Request,Depends
from app.config import missing_config
from app.database import connect
from app.storage.r2 import S3StorageGateway

def local_only(request:Request):
    missing_config()
    if os.getenv('APP_ENV')!='development' or not request.client or request.client.host not in ('127.0.0.1','::1','testclient'):
        raise HTTPException(403,'Local review only')
    if request.headers.get('origin') not in (None,'http://localhost:5173','http://127.0.0.1:5173'):
        raise HTTPException(403,'Origin not allowed')
    if request.headers.get('host','').split(':')[0] not in ('localhost','127.0.0.1','testserver'):
        raise HTTPException(403,'Host not allowed')

router=APIRouter(prefix='/api/v1/review',dependencies=[Depends(local_only)])

@router.get('/versions')
def versions():
    with connect() as conn:
        rows=conn.execute("SELECT v.id,v.edition FROM dialogue.video_versions v WHERE v.status <> 'withdrawn' AND EXISTS(SELECT 1 FROM dialogue.subtitle_revisions r WHERE r.version_id=v.id) ORDER BY v.created_at DESC").fetchall()
    return [{'id':str(x[0]),'edition':x[1]} for x in rows]

@router.get('/{version}')
def review(version:UUID):
    with connect() as conn:
        report=conn.execute("SELECT payload FROM dialogue.jobs WHERE type='import' AND status='ready' AND payload->>'version_id'=%s ORDER BY created_at DESC LIMIT 1",(str(version),)).fetchone()
        if not report:raise HTTPException(404,'Import not found')
        report=report[0]
        rows=conn.execute("SELECT id,start_ms,end_ms,text_en,text_zh FROM dialogue.segments WHERE revision_id=%s ORDER BY seq",(report['revision_id'],)).fetchall()
        asset=conn.execute('SELECT a.bucket,a.object_key FROM dialogue.video_versions v JOIN dialogue.assets a ON a.id=coalesce(v.proxy_asset_id,v.source_asset_id) WHERE v.id=%s',(version,)).fetchone()
    if not asset:raise HTTPException(404,'Media not found')
    return {'report':report,'segments':[{'id':str(x[0]),'start_ms':x[1],'end_ms':x[2],'en':x[3],'zh':x[4]} for x in rows],'media_url':S3StorageGateway().sign_read(*asset,ttl=3600)}
