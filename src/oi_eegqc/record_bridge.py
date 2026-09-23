"""Views of the common AppData list. No replicated lists or acknowledgement queue."""
import hashlib
import logging
import os
from pathlib import Path
from PySide6.QtCore import QObject,QTimer,QModelIndex
from PySide6.QtNetwork import QLocalServer,QLocalSocket
from .record_link import RecordHub,contains,record_id


from .window_activation import server_name,notify_existing,show_window


class SharedRecordView(QObject):
    def __init__(self,controller,window=None,hub=None):
        super().__init__(controller)
        self.c=controller;self.window=window;self.hub=hub or RecordHub()
        self.attempted=set();self.hidden=set();self.timer=QTimer(self);self.timer.setInterval(800)
        self.timer.timeout.connect(self.refresh);self.server=None
        controller.imported.connect(self.imported)
        controller._upload.record_guard=self.upload_allowed
        self.timer.start()

    def listen(self):
        self.server=QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self.server.listen(server_name(self.hub.path)):
            raise RuntimeError('EEGQC 记录接口已由另一实例使用')
        self.server.newConnection.connect(self.activate)

    def activate(self):
        import json
        while self.server.hasPendingConnections():
            socket=self.server.nextPendingConnection()
            socket.write((json.dumps({'pid':os.getpid()})+'\n').encode());socket.flush()
            def command(s=socket):
                if not s.canReadLine():return
                if bytes(s.readLine()).strip()==b'activate':
                    if self.window:show_window(self.window)
                    s.write(b'shown\n');s.flush()
                # Activating a window must not re-import or reorder its rows.
            socket.readyRead.connect(command)
            socket.disconnected.connect(socket.deleteLater)

    def matching(self,record):
        return [r for r in self.c.files.rows if contains(record['root'],r['path'])]

    def score_settings(self):
        from . import __version__
        return [__version__,self.c._order,self.c._mains,self.c._all_channels,self.c._selected_channels]

    def imported(self):
        # Manual EEGQC imports participate in the same protocol, too.
        records=self.hub.rows()
        from .application.qt_workers import ReportView
        for record in records:
            if record['removed']:continue
            if record['qc'].get('settings')!=self.score_settings():continue
            saved={f.get('path'):f for f in record['qc'].get('files',[])}
            for i,row in enumerate(self.c.files.rows):
                prior=saved.get(row['path'])
                if not prior or not prior.get('report') or row['report'] is not None:continue
                stat=Path(row['path']).stat()
                if prior.get('stat')==[stat.st_size,stat.st_mtime_ns]:
                    self.c.files.patch(i,report=ReportView(prior['report']),score=prior['score'],state='完成',ready=True,
                        scored_stat=tuple(prior['stat']),content_sha256=prior.get('sha256'))
        for root in self.c._folder_roots or [r['path'] for r in self.c.files.rows]:
            if any(contains(r['root'],root) or contains(root,r['root']) for r in records):continue
            identifier=self.hub.publish(record_id('eegqc',os.path.normcase(root)),root,'eegqc',
                {'participant':Path(root).name,'mode':'external','status':'imported','count':0})
        self.refresh()

    def remove_paths(self,paths):
        for r in self.hub.rows():
            if any(contains(r['root'],p) for p in paths):self.hub.remove(r['id'])

    def upload_allowed(self,roots):
        return not any(r['removed'] and any(contains(root,r['root']) or contains(r['root'],root) for root in roots) for r in self.hub.rows())

    def refresh(self):
        try:self._refresh()
        except Exception:
            logging.exception('Shared record view failed')
            self.c._notice='无法读取公共名单，请检查 AppData 目录权限';self.c.changed.emit()

    def _refresh(self):
        c=self.c
        if c._closing:return
        records=self.hub.rows()
        self.hidden.intersection_update(r['id'] for r in records if r['removed'])
        removed=[r for r in records if r['removed'] and (r['id'] not in self.hidden or self.matching(r))]
        if removed:
            if c._upload.active:
                c._upload.cancel();return
            if c._busy:return
            roots=[r['root'] for r in removed]
            if not c._upload.detachLocalRecords(roots):return
            for i in range(len(c.files.rows)-1,-1,-1):
                if any(contains(root,c.files.rows[i]['path']) for root in roots):
                    c.files.beginRemoveRows(QModelIndex(),i,i);c.files.rows.pop(i);c.files.endRemoveRows()
            c.files.path_indices={os.path.normcase(r['path']):i for i,r in enumerate(c.files.rows)}
            c._folder_roots=[root for root in c._folder_roots if not any(contains(r,root) for r in roots)]
            c.files.names(c._folder_roots)
            for r in removed:self.hidden.add(r['id']);self.attempted.discard(r['id'])
            c._notice='已从公共名单移除，原始文件和已上传数据保留';c.changed.emit()
        if c._busy or c._upload.active:return
        for r in records:
            if r['removed']:continue
            members=self.matching(r)
            if members:
                self.hub.status(r['id'],'qc',{'state':'scored' if all(x['report'] is not None for x in members) else 'loaded','settings':self.score_settings(),
                    'files':[{'name':Path(x['path']).name,'path':x['path'],'score':x['score'],'state':x['state'],
                              'stat':x.get('scored_stat'),'sha256':x.get('content_sha256'),
                              'report':x['report'].to_dict() if x['report'] else None} for x in members]})
            batch=c._upload.batch or {}
            for part in batch.get('children') or [batch]:
                if any(contains(root,r['root']) or contains(r['root'],root) for root in part.get('roots',[])):
                    self.hub.status(r['id'],'upload',{'state':part.get('status',''),'upload_id':part.get('upload_id'),'round_id':part.get('round_id')})
            if r['id'] not in self.attempted:
                self.attempted.add(r['id'])
                if not Path(r['root']).exists():
                    self.hub.status(r['id'],'qc',{'state':'missing'});continue
                self.hub.status(r['id'],'qc',{**r['qc'],'state':'loading'})
                c.add_paths([r['root']]);return
