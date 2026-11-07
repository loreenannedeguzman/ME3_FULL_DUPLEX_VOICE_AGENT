"""Read-only delivery/bundling boundaries; no generated code executes here."""
import io,json,unittest,zipfile
from unittest.mock import Mock
from me3_assistant.workspace import WorkspaceError,digest
from me3_assistant.artifact_delivery import ArtifactDelivery,PREVIEW_CSP,manifest

class Delivery(unittest.TestCase):
 def setUp(self):
  self.files={'game/index.html':b'<!doctype html><html><head><link rel="stylesheet" href="style.css"></head><body><canvas id="board" width="320" height="200"></canvas><button id="play" onclick="startGame()">Play</button><script src="game.js"></script></body></html>','game/style.css':b'body {background:#123;color:white}', 'game/game.js':b'function startGame(){document.getElementById("play").textContent="Playing";}','notes/outline.md':b'# Outline\nOne paragraph.'};self.workspace=Mock();self.workspace.files.side_effect=lambda:dict(self.files);self.delivery=ArtifactDelivery(self.workspace)
 def test_listing_exposes_relative_versions_not_host_paths(self):
  result=self.delivery.listing();self.assertEqual(result['workspace_sha256'],manifest(self.files));self.assertEqual(result['total_files'],4);self.assertTrue(next(x for x in result['files'] if x['path']=='game/index.html')['previewable']);self.assertNotIn('root',result)
 def test_download_exact_bytes_attachment_and_SHA(self):
  raw=self.files['notes/outline.md'];result=self.delivery.download('notes/outline.md',digest(raw));self.assertEqual(result['body'],raw);self.assertEqual(result['filename'],'outline.md');self.assertTrue(result['attachment'])
 def test_stale_download_cannot_export_changed_content(self):
  version=digest(self.files['notes/outline.md']);self.files['notes/outline.md']=b'changed'
  with self.assertRaisesRegex(WorkspaceError,'artifact_version_changed'):self.delivery.download('notes/outline.md',version)
 def test_arbitrary_host_paths_refused(self):
  for path in ['/etc/passwd','../../secret.txt','C:/secret.txt','data/.env']:
   with self.assertRaises(WorkspaceError):self.delivery.download(path,'a'*64)
 def test_ZIP_uses_current_bound_snapshot_without_generated_executables(self):
  result=self.delivery.project_zip(manifest(self.files),'game')
  with zipfile.ZipFile(io.BytesIO(result['body'])) as archive:
   self.assertEqual(archive.namelist(),sorted(n for n in self.files if n.startswith('game/')))
   for name in archive.namelist():self.assertEqual(archive.read(name),self.files[name])
  self.assertEqual(result['filename'],'game.zip');self.assertTrue(result['attachment'])
 def test_ZIP_stale_workspace_refused(self):
  with self.assertRaisesRegex(WorkspaceError,'artifact_workspace_changed'):self.delivery.project_zip('a'*64)
 def test_local_game_CSS_JS_bundled_for_opaque_iframe(self):
  result=self.delivery.preview('game/index.html',manifest(self.files));self.assertEqual(result['sandbox'],'allow-scripts');self.assertNotIn('allow-same-origin',result['sandbox']);self.assertIn('function startGame()',result['html']);self.assertIn('onclick="startGame()"',result['html']);self.assertIn('background:#123',result['html']);self.assertIn('<canvas',result['html']);self.assertNotIn('src="game.js"',result['html']);self.assertEqual(result['included_scripts'],1);self.assertEqual({x['path'] for x in result['assets']},{'game/index.html','game/style.css','game/game.js'})
 def test_external_declarative_assets_forms_navigation_removed(self):
  self.files['attack.html']=b'<base href="https://bad.test"><meta http-equiv="refresh" content="0;https://bad.test"><iframe src="/api/state"></iframe><object data="file:///etc/passwd"></object><form action="/api/command"><input name="x"><button formaction="https://bad.test">Submit</button></form><a href="https://bad.test" target="_top" ping="https://bad.test">Open</a><script src="https://bad.test/x.js"></script><link rel="stylesheet" href="https://bad.test/x.css"><img src="https://bad.test/steal">'
  result=self.delivery.preview('attack.html',manifest(self.files));html=result['html'];self.assertNotIn('https://bad.test',html);self.assertNotIn('/api/state',html);self.assertNotIn('file://',html);self.assertNotIn('<form',html);self.assertNotIn('formaction',html);self.assertNotIn('target=',html);self.assertEqual(result['blocked_scripts'],1);self.assertEqual(result['blocked_stylesheets'],1)
 def test_preview_CSP_blocks_network_and_parent_privileges(self):
  self.assertIn("default-src 'none'",PREVIEW_CSP);self.assertIn("connect-src 'none'",PREVIEW_CSP);self.assertIn("form-action 'none'",PREVIEW_CSP);self.assertIn("frame-src 'none'",PREVIEW_CSP);self.assertNotIn("'self'",PREVIEW_CSP)
 def test_script_closing_text_cannot_escape_bundled_script(self):
  self.files['game/game.js']=b'const text="</script><img src=https://bad.test>";';result=self.delivery.preview('game/index.html',manifest(self.files));self.assertIn('<\\/script>',result['html']);self.assertNotIn('</script><img',result['html'])
 def test_local_asset_traversal_outside_workspace_refused(self):
  self.files['attack.html']=b'<script src="../outside.js"></script><script src="file:///etc/passwd"></script><link rel="stylesheet" href="%2e%2e/secret.css">';result=self.delivery.preview('attack.html',manifest(self.files));self.assertEqual(result['blocked_scripts'],2);self.assertEqual(result['blocked_stylesheets'],1)
 def test_preview_workspace_CAS_covers_CSS_JS_and_HTML(self):
  version=manifest(self.files);self.files['game/game.js']+=b'changed'
  with self.assertRaisesRegex(WorkspaceError,'artifact_workspace_changed'):self.delivery.preview('game/index.html',version)
 def test_backend_source_is_downloadable_but_not_claimed_web_preview(self):
  self.files['app.py']=b'print("not executed")';self.assertEqual(self.delivery.download('app.py',digest(self.files['app.py']))['body'],self.files['app.py'])
  with self.assertRaisesRegex(WorkspaceError,'static_HTML_preview_required'):self.delivery.preview('app.py',manifest(self.files))
 def test_missing_assets_reported_without_fetching_or_inventing_content(self):
  del self.files['game/game.js'];result=self.delivery.preview('game/index.html',manifest(self.files));self.assertEqual(result['blocked_scripts'],1);self.assertEqual(result['included_scripts'],0);self.assertNotIn('function startGame()',result['html'])
if __name__=='__main__':unittest.main()
