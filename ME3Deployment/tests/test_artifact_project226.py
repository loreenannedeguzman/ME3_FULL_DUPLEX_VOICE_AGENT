"""Authenticated project-only export; real ZIP bytes, no network or effects."""
import io,json,unittest,zipfile
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode
from me3_assistant.artifact_delivery import ArtifactDelivery,manifest
from onit_candidate.artifact_gui import handle_get
class Handler:
 def __init__(self,path,session=True):self.path=path;self.headers={};self.server=SimpleNamespace(server_port=8765,console=None);self.wfile=io.BytesIO();self.status=None;self.out={};self.body=None;self.session=session
 def host_ok(self):return True
 def session_ok(self):return self.session
 def reply(self,status,value):self.status=status;self.body=value
 def send_response(self,status):self.status=status
 def send_header(self,name,value):self.out[name]=value
 def end_headers(self):pass
class ProjectExportChecks(unittest.TestCase):
 def setUp(self):
  self.files={'wb09-delivery226/README.md':b'Verified project instructions.','wb09-delivery226/service.py':b'print("synthetic source, never executed")','other-project/private-note.md':b'Unrelated artifact must not be in project ZIP.'};workspace=Mock();workspace.files.side_effect=lambda:dict(self.files);self.delivery=ArtifactDelivery(workspace);self.version=manifest(self.files)
 def request(self,params,session=True):
  handler=Handler('/api/artifacts/project.zip?'+urlencode(params,doseq=True),session=session);self.assertTrue(handle_get(handler,lambda _:self.delivery));return handler
 def test_project_ZIP_contains_exact_selected_folder_and_no_other_artifacts(self):
  handler=self.request(dict(workspace_sha256=self.version,directory='wb09-delivery226'));self.assertEqual(handler.status,200);self.assertEqual(handler.out['Content-Disposition'],'attachment; filename="wb09-delivery226.zip"')
  with zipfile.ZipFile(io.BytesIO(handler.wfile.getvalue())) as archive:
   self.assertEqual(set(archive.namelist()),{'wb09-delivery226/README.md','wb09-delivery226/service.py'})
   for name in archive.namelist():self.assertEqual(archive.read(name),self.files[name])
 def test_legacy_explicit_whole_workspace_export_still_works(self):
  handler=self.request(dict(workspace_sha256=self.version));self.assertEqual(handler.status,200)
  with zipfile.ZipFile(io.BytesIO(handler.wfile.getvalue())) as archive:self.assertEqual(set(archive.namelist()),set(self.files))
 def test_stale_snapshot_rejects_instead_of_exporting_new_content(self):
  self.files['wb09-delivery226/service.py']+=b'changed';self.assertEqual(self.request(dict(workspace_sha256=self.version,directory='wb09-delivery226')).status,409)
 def test_directory_traversal_absolute_hidden_or_missing_is_rejected(self):
  for directory in ('../other-project','/etc','C:/Users','.versions','not-a-project'):
   handler=self.request(dict(workspace_sha256=self.version,directory=directory));self.assertEqual(handler.status,409);self.assertEqual(handler.body,{'error':'artifact_changed_or_unavailable_refresh_list'})
 def test_unknown_duplicate_or_missing_query_fields_rejected(self):
  for params in (dict(directory='wb09-delivery226'),dict(workspace_sha256=self.version,directory=['wb09-delivery226','other-project']),dict(workspace_sha256=self.version,directory='wb09-delivery226',root='/etc')):self.assertEqual(self.request(params).status,409)
 def test_authentication_checked_before_any_workspace_read(self):
  handler=Handler('/api/artifacts/project.zip?'+urlencode(dict(workspace_sha256=self.version,directory='wb09-delivery226')),session=False);factory=Mock(side_effect=AssertionError('Unauthorized artifact read'));handle_get(handler,factory);self.assertEqual(handler.status,403);factory.assert_not_called()
if __name__=='__main__':unittest.main(verbosity=2)
