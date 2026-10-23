import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from xovium.core import Context, Error
from xovium import terrain_sources as terrain


class TerrainReleases(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ctx = Context(self.root / 'installer', SimpleNamespace(
            prefix=str(self.root / 'installed'), dry_run=False, action='install', jobs=1))
        self.ctx.save = lambda: None  # Test state stays in memory; no user's location file is touched.
        self.network = []
        self.ctx.run = self.download
        self.asset = 'gz-dynamic-terrain-plugin-v0.3.0.zip'
        self.archive_root = self.asset[:-4]
        self.manifest = self.root / 'releases.json'
        self.patch = patch.object(terrain, 'RELEASES', self.manifest)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.make_zip()

    def make_zip(self, extra=None):
        self.fixture = self.root / 'fixture.zip'
        with zipfile.ZipFile(self.fixture, 'w') as zipped:
            for name, data in {'README.md':'terrain', 'LICENSE':'BSD', 'build.sh':'#!/bin/sh\n', **(extra or {})}.items():
                entry = zipfile.ZipInfo(self.archive_root + '/' + name)
                entry.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
                zipped.writestr(entry, data)
        self.pin = {'repository':'Xovium-Tech/gz-dynamic-terrain-plugin', 'tag':'v0.3.0',
                    'asset':self.asset, 'sha256':hashlib.sha256(self.fixture.read_bytes()).hexdigest()}
        self.manifest.write_text(json.dumps({'schema':1,'plugins':{'gazebo':self.pin}}))

    def download(self, *args, **unused):
        self.assertEqual(args[0], 'curl')
        self.assertEqual(args[-1], 'https://github.com/Xovium-Tech/gz-dynamic-terrain-plugin/releases/download/v0.3.0/' + self.asset)
        self.network.append(args[-1])
        shutil.copyfile(self.fixture, args[args.index('--output') + 1])

    def test_verified_install_and_offline_reuse(self):
        target = terrain.install_source(self.ctx, 'gazebo', 'plugin')
        self.assertTrue((target / 'build.sh').stat().st_mode & 0o111)
        self.assertEqual(self.ctx.state['terrain_releases']['gazebo'], self.pin)
        self.fixture.unlink()
        terrain.install_source(self.ctx, 'gazebo', 'plugin')
        self.assertEqual(len(self.network), 1)

    def test_corrupt_download_cache_redownloads(self):
        terrain.verify(self.ctx, 'gazebo')
        cached = next((self.ctx.prefix / 'cache').rglob('*.zip'))
        cached.write_bytes(b'broken')
        terrain.verify(self.ctx, 'gazebo')
        self.assertEqual(len(self.network), 2)

    def test_corrupt_extraction_repairs_from_verified_zip(self):
        source = terrain.verify(self.ctx, 'gazebo')
        (source / 'README.md').write_text('broken')
        (source / 'extra.txt').write_text('extra')
        self.fixture.unlink()
        fixed = terrain.verify(self.ctx, 'gazebo')
        self.assertEqual((fixed / 'README.md').read_text(), 'terrain')
        self.assertFalse((fixed / 'extra.txt').exists())
        self.assertEqual(len(self.network), 1)

    def test_checksum_failure_never_installs(self):
        self.fixture.write_bytes(b'bad download')
        with self.assertRaises(Error): terrain.install_source(self.ctx, 'gazebo', 'plugin')
        self.assertFalse((self.ctx.prefix / 'plugin').exists())

    def test_owned_edits_block_before_any_replacement(self):
        target = terrain.install_source(self.ctx, 'gazebo', 'plugin')
        (target / 'README.md').write_text('my edit')
        before = (target / 'build.sh').stat().st_mtime_ns
        self.make_zip({'build.sh':'#!/bin/sh\nexit 0\n'})
        with self.assertRaisesRegex(Error, 'Locally modified'): terrain.install_source(self.ctx, 'gazebo', 'plugin')
        self.assertEqual((target / 'README.md').read_text(), 'my edit')
        self.assertEqual((target / 'build.sh').stat().st_mtime_ns, before)

    def test_obsolete_owned_sources_removed_custom_files_kept(self):
        self.make_zip({'old.h':'old header'})
        target = terrain.install_source(self.ctx, 'gazebo', 'plugin')
        (target / 'custom.txt').write_text('mine')
        self.make_zip({'new.h':'new header'})
        terrain.install_source(self.ctx, 'gazebo', 'plugin')
        self.assertFalse((target / 'old.h').exists())
        self.assertEqual((target / 'custom.txt').read_text(), 'mine')
        self.assertEqual((target / 'new.h').read_text(), 'new header')

    def test_unsafe_archive_rejected_before_writes(self):
        for name, mode in [('../outside', 0o100644), ('link', 0o120777), ('dir\\outside', 0o100644)]:
            with self.subTest(name=name):
                self.make_zip()
                with zipfile.ZipFile(self.fixture, 'a') as zipped:
                    entry = zipfile.ZipInfo(self.archive_root + '/' + name)
                    entry.external_attr = mode << 16
                    zipped.writestr(entry, '/tmp/outside')
                self.pin['sha256'] = hashlib.sha256(self.fixture.read_bytes()).hexdigest()
                self.manifest.write_text(json.dumps({'schema':1,'plugins':{'gazebo':self.pin}}))
                with self.assertRaises(Error): terrain.install_source(self.ctx, 'gazebo', 'plugin')
                self.assertFalse((self.ctx.prefix / 'plugin').exists())


if __name__ == '__main__': unittest.main()
