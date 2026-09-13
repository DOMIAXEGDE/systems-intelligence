import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from framelm.core import DEFAULTS, Pipeline, SQLiteRetriever, frame_prompt
from framelm.generator import Recipe, escaped_field, fnv1a64
from framelm.context_store import ContextStore, ConflictError, Cancelled
from framelm.studio import make_server


class GeneratorTests(unittest.TestCase):
    def test_cartesian_order_and_window(self):
        r = Recipe(mode="cartesian", input_value="01", width=3, start=2, limit=3)
        self.assertEqual([x["provenance"]["payload"] for x in r.records()], ["010","011","100"])
        self.assertEqual(r.plan()["next_start"], "5")

    def test_numeric_padding(self):
        r = Recipe(mode="numeric", width=4, start=98, limit=3)
        self.assertEqual([x["provenance"]["payload"] for x in r.records()], ["0098","0099","0100"])

    def test_repeat_emits_records(self):
        r = Recipe(mode="repeat", input_value="AB", width=3)
        rows = list(r.records())
        self.assertEqual([x["body"] for x in rows], ["AB"] * 3)
        self.assertEqual(len({x["generation_key"] for x in rows}), 3)

    def test_unicode_reverse_and_hash(self):
        row = next(Recipe(mode="reverse", input_value="café🙂", prefix="[", suffix="]").records())
        self.assertEqual(row["body"], "[🙂éfac]")
        self.assertEqual(row["provenance"]["fnv1a64"], fnv1a64("[🙂éfac]"))

    def test_whitespace_and_json_roundtrip(self):
        r = Recipe(input_value="a\n\tb\\c", prefix="<", suffix=">")
        row = next(r.records())
        self.assertEqual(json.loads(json.dumps(row))["body"], "<a\n\tb\\c>")
        self.assertEqual(escaped_field("\n\t\\\x00"), "\\n\\t\\\\\\x00")

    def test_overflow_and_large_batch_guards(self):
        for r in [Recipe(mode="symbols",width=16), Recipe(mode="numeric",width=6,all=True)]:
            with self.assertRaises(ValueError): r.plan()
        self.assertEqual(Recipe(mode="numeric",width=6,limit=100001,allow_large=True).plan()["count"],100001)

    def test_invalid_templates_rejected(self):
        for template in ["{value.__class__}","{value!r}","{unknown}","{"]:
            with self.assertRaises(ValueError): Recipe(body_template=template).plan()

    def test_64bit_ordinals_survive_json_strings(self):
        r = Recipe.from_dict({"mode":"numeric","width":"18","start":"9007199254740993","limit":"1"})
        row = next(r.records())
        self.assertEqual(row["provenance"]["ordinal"], "9007199254740993")
        self.assertEqual(row["body"], "009007199254740993")

    def test_zero_limit_and_start_after_end(self):
        self.assertEqual(list(Recipe(limit=0).records()), [])
        self.assertEqual(list(Recipe(start=2).records()), [])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / "contexts"; self.docs.mkdir()
        self.path = self.root / "contexts.db"
        self.store = ContextStore(self.path)
        self.index = SQLiteRetriever(self.path)
        self.config = DEFAULTS | {"index_path": str(self.path), "context_dir": str(self.docs)}

    def test_append_edit_disable_reenable_retrieval(self):
        item = self.store.append({"title":"First", "body":"Zirconium is the test context."})
        self.assertTrue(self.index.search(frame_prompt("zirconium"), 5))
        item = self.store.edit(item["id"],item["revision"], item | {"body":"Hafnium replaces the original text."})
        self.assertFalse(self.index.search(frame_prompt("zirconium"),5))
        item = self.store.toggle(item["id"],item["revision"],False)
        self.assertEqual(Pipeline(self.config).run("hafnium")["status"],"insufficient_context")
        self.index.build(self.docs)
        self.assertFalse(self.index.search(frame_prompt("hafnium"),5))
        self.store.toggle(item["id"],item["revision"],True)
        self.assertTrue(self.index.search(frame_prompt("hafnium"),5))

    def test_adopted_file_does_not_resurrect(self):
        (self.docs/"sample.txt").write_text("Zirconium reference",encoding="utf-8")
        self.index.build(self.docs)
        self.store.adopt_files(self.docs)
        item = self.store.get(self.store.list()["items"][0]["id"])
        self.store.toggle(item["id"],item["revision"],False)
        self.index.build(self.docs)
        self.store.adopt_files(self.docs)
        self.assertFalse(self.index.search(frame_prompt("zirconium"),5))
        self.assertEqual(self.store.list()["stats"]["disabled"],1)

    def test_regeneration_preserves_edit_and_disable(self):
        recipe = Recipe(mode="numeric",width=3,limit=3)
        self.assertEqual(self.store.append_generated(recipe)["added"],3)
        item = self.store.get(self.store.list()["items"][0]["id"])
        self.store.edit(item["id"],1,item | {"body":"Curated correction", "enabled":False})
        result = self.store.append_generated(Recipe(mode="numeric",width=3,start=1,limit=4))
        self.assertEqual(result["added"],2)
        self.assertEqual(result["skipped"],2)
        preserved = self.store.get(item["id"])
        self.assertFalse(preserved["enabled"])
        self.assertEqual(preserved["body"],"Curated correction")

    def test_stale_edit_is_rejected(self):
        item = self.store.append({"title":"Title","body":"Original"})
        self.store.edit(item["id"],1,item | {"body":"New text"})
        with self.assertRaises(ConflictError): self.store.edit(item["id"],1,item)
        self.assertEqual(self.store.get(item["id"])["body"],"New text")

    def test_cancel_rolls_back_batch_and_index(self):
        event = threading.Event()
        def progress(_): event.set()
        with self.assertRaises(Cancelled):
            self.store.append_generated(Recipe(mode="repeat",input_value="Zirconium",width=3),event,progress)
        self.assertEqual(self.store.list()["total"],0)
        self.assertFalse(self.index.search(frame_prompt("zirconium"),5))

    def test_validation_failure_rolls_back_batch(self):
        # Second payload is whitespace, invalid with the literal body template.
        with self.assertRaises(ValueError):
            self.store.append_generated(Recipe(mode="cartesian",input_value="a ",width=1))
        self.assertEqual(self.store.list()["total"],0)

    def test_restart_and_export_preserve_unicode_and_status(self):
        item = self.store.append({"title":"café","body":"Line 1\nLine 2 🙂", "enabled":False})
        reopened = ContextStore(self.path)
        exported = json.loads(next(reopened.export_jsonl()))
        self.assertEqual(exported["body"],item["body"])
        self.assertFalse(exported["enabled"])

    def test_literal_filter_and_pagination(self):
        for title in ["100%","other","final"]:
            self.store.append({"title":title,"body":"Body"})
        self.assertEqual(self.store.list(query="%")["total"],1)
        self.assertEqual(len(self.store.list(page=1,page_size=2)["items"]),1)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name); (root/"contexts").mkdir()
        self.server = make_server(DEFAULTS | {"index_path":str(root/"index.db"),"context_dir":str(root/"contexts")},0)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def post(self,path,data,token=True):
        request = Request(self.base+path,data=json.dumps(data).encode(),headers={"Content-Type":"application/json", **({"X-Studio-Token":self.server.app.token} if token else {})})
        with urlopen(request) as response: return json.load(response)

    def test_api_lifecycle_and_local_retrieval(self):
        row = self.post('/api/append',{'title':'Test reference','body':'Zirconium is a retrieval test.','enabled':True})
        self.assertEqual(self.post('/api/ask',{'prompt':'zirconium'})['status'],'resolved')
        self.post('/api/toggle',{'id':row['id'],'revision':row['revision'],'enabled':False})
        self.post('/api/reindex',{})
        self.assertEqual(self.post('/api/ask',{'prompt':'zirconium'})['status'],'insufficient_context')

    def test_csrf_required(self):
        with self.assertRaises(HTTPError) as error:
            self.post('/api/append',{'title':'Test','body':'Test'},token=False)
        self.assertEqual(error.exception.code,403)

    def test_preview_string_ordinals_and_assets(self):
        data = self.post('/api/preview',{'mode':'numeric','width':'3','start':'7','limit':'2'})
        self.assertEqual(data['records'][0]['body'],'007')
        for path in ['/','/app.js','/style.css']:
            with urlopen(self.base+path) as r:
                self.assertEqual(r.status,200)
                self.assertGreater(len(r.read()),100)

    def test_background_generation_commits_and_is_idempotent(self):
        recipe = {'mode':'numeric','width':'3','start':'7','limit':'3'}
        self.post('/api/generate',recipe)
        self.server.app.worker.join(timeout=5)
        self.assertFalse(self.server.app.worker.is_alive())
        with urlopen(self.base+'/api/job') as response:
            self.assertEqual(json.load(response)['added'],3)
        self.post('/api/generate',recipe)
        self.server.app.worker.join(timeout=5)
        with urlopen(self.base+'/api/job') as response:
            result = json.load(response)
            self.assertEqual(result['added'],0)
            self.assertEqual(result['skipped'],3)


@unittest.skipUnless(shutil.which('g++'), 'C++ compiler unavailable')
class CppParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binaries = {}
        root = Path(__file__).resolve().parents[1]
        for name in ['4','5','6']:
            binary = Path(cls.temp.name)/name
            subprocess.run(['g++','-std=c++17','-O2',str(root/'reference_sources'/f'{name}.cpp'),'-o',str(binary)],check=True,capture_output=True)
            cls.binaries[name] = binary

    def output(self,name,options):
        raw = subprocess.run([str(self.binaries[name]),'--execute',*options],check=True,capture_output=True,text=True).stdout
        return [line.split('\t') for line in raw.splitlines() if line.startswith('record\t')]

    def test_4_symbol_values_and_hashes_match(self):
        rows = self.output('4',['--width','2','--alphabet','original','--start','60','--limit','10','--output','-'])
        generated = list(Recipe(mode='symbols',alphabet='original',width=2,start=60,limit=10).records())
        for cpp,py in zip(rows,generated):
            self.assertEqual(cpp[4],py['provenance']['fnv1a64'])
            self.assertEqual(cpp[5],escaped_field(py['provenance']['payload']))
        self.assertEqual(len(rows),len(generated))

    def test_5_numeric_values_and_hashes_match(self):
        rows = self.output('5',['--width','7','--start','9999','--limit','3','--output','-'])
        generated = list(Recipe(mode='numeric',width=7,start=9999,limit=3).records())
        self.assertEqual([(r[4],r[5]) for r in rows],[(r['provenance']['fnv1a64'],r['provenance']['payload']) for r in generated])

    def test_6_all_four_flows_match_ascii_semantics(self):
        for mode in ['cartesian','literal','repeat','reverse']:
            with self.subTest(mode=mode):
                value = '01' if mode == 'cartesian' else 'Ab\\nCd'
                recipe = Recipe(mode=mode,input_value='01' if mode=='cartesian' else 'Ab\nCd',width=3,prefix='<',suffix='>',limit=10)
                rows = self.output('6',['--object-name','test','--input-value',value,'--input-width','3','--flow-type',mode,'--prefix','<','--suffix','>','--limit','10','--output-target','-'])
                generated = list(recipe.records())
                self.assertEqual([(r[8],r[9],r[10]) for r in rows],[(r['provenance']['fnv1a64'],escaped_field(r['provenance']['payload']),escaped_field(r['provenance']['rendered'])) for r in generated])


if __name__ == '__main__': unittest.main()
