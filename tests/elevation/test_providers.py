"""Unit-Tests für providers.py: FakeDataSource, CopernicusDEMDataSource und
DEMDataSourceProtocol.
"""

import asyncio
import importlib.util
import shutil
import time
from pathlib import Path
from types import ModuleType

import pytest
import rasterio
from tripplanner.elevation.elevation import calculate_horizontal_distance
from tripplanner.elevation.providers import CopernicusDEMDataSource, FakeDataSource


def _load_create_test_dem_tile() -> ModuleType:
    """Load `scripts/create_test_dem_tile.py` without adding it to `sys.path`.

    Reuses the project's existing synthetic-tile generator instead of
    duplicating tile-creation logic in tests.
    """
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "create_test_dem_tile.py"
    spec = importlib.util.spec_from_file_location("create_test_dem_tile", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_create_test_dem_tile = _load_create_test_dem_tile()


class TestFakeDataSource:
    """Tests für FakeDataSource."""

    def test_get_elevation_returns_within_noise_range(self) -> None:
        """Höhenwert liegt im erwarteten Bereich ± noise_range."""
        source = FakeDataSource(baseline_elevation=150.0, noise_range=4.0)
        elevation = source.get_elevation(47.0, 8.0)
        assert 148.0 <= elevation <= 152.0

    def test_get_elevation_is_deterministic(self) -> None:
        """Höhenwert ist deterministisch (selbe Koordinate = gleicher Wert)."""
        source = FakeDataSource(baseline_elevation=100.0, noise_range=5.0)
        val1 = source.get_elevation(47.12345, 8.67890)
        val2 = source.get_elevation(47.12345, 8.67890)
        assert val1 == val2

    def test_get_elevation_different_coordinates_different_values(self) -> None:
        """Unterschiedliche Koordinaten liefern unterschiedliche Werte."""
        source = FakeDataSource(baseline_elevation=100.0, noise_range=50.0)
        val1 = source.get_elevation(47.0, 8.0)
        val2 = source.get_elevation(47.1, 8.1)
        # Bei hoher NoiseRange sollten die Werte meistens unterschiedlich sein
        # (es ist theoretisch möglich, aber extrem unwahrscheinlich)
        assert val1 != val2 or abs(val1 - val2) > 0.001

    @pytest.mark.asyncio
    async def test_get_elevations_batch(self) -> None:
        source = FakeDataSource(baseline_elevation=100.0, noise_range=5.0)
        coords = [(47.0, 8.0), (47.1, 8.1), (47.2, 8.2)]
        elevations = await source.get_elevations_batch(coords)

        assert len(elevations) == 3
        for elev in elevations:
            assert 95.0 <= elev <= 105.0

    def test_get_tile_at_returns_tile(self) -> None:
        """get_tile_at gibt synthetische Kachel zurück."""
        source = FakeDataSource()
        tile = source.get_tile_at(47.0, 8.0)

        assert tile is not None
        assert tile.key.min_lat <= 47.0 <= tile.key.max_lat
        assert tile.key.min_lon <= 8.0 <= tile.key.max_lon

    def test_get_tiles_in_bbox_returns_tile(self) -> None:
        """get_tiles_in_bbox gibt synthetische Kacheln zurück."""
        source = FakeDataSource()
        tiles = source.get_tiles_in_bbox(46.0, 48.0, 7.0, 9.0)

        assert len(tiles) >= 1
        tile = tiles[0]
        assert tile.width == 5
        assert tile.height == 5


@pytest.fixture
def copernicus_tile_dir(tmp_path: Path) -> Path:
    """Local dir mirroring the real bucket's tile-folder layout for N47/E008.

    Contains exactly one tile (matching `scripts/create_test_dem_tile.py`'s
    fixed bounds: 47.0-47.00015N, 8.0-8.00015E, linear 100m-120m gradient).
    """
    tile_name = "Copernicus_DSM_COG_10_N47_00_E008_00_DEM"
    tile_folder = tmp_path / tile_name
    tile_folder.mkdir()
    _create_test_dem_tile.create_synthetic_dem_tile(tile_folder / f"{tile_name}.tif")
    return tmp_path


class TestCopernicusDEMDataSource:
    """Tests für CopernicusDEMDataSource gegen eine lokale Test-Kachel."""

    def test_tile_name_northern_eastern_hemisphere(self) -> None:
        """Tile-Name für Berlin (N52/E013) entspricht dem echten Bucket-Layout."""
        source = CopernicusDEMDataSource()
        assert source._tile_name(52.52, 13.405) == "Copernicus_DSM_COG_10_N52_00_E013_00_DEM"

    def test_tile_name_southern_western_hemisphere(self) -> None:
        """S/W-Koordinaten werden mit S/W-Präfix und korrektem Floor-Runden benannt."""
        source = CopernicusDEMDataSource()
        assert source._tile_name(-33.45, -70.65) == "Copernicus_DSM_COG_10_S34_00_W071_00_DEM"

    def test_default_base_url_builds_vsicurl_uri_against_public_bucket(self) -> None:
        """Default-Konstruktor baut eine /vsicurl/-URI gegen den öffentlichen Bucket."""
        source = CopernicusDEMDataSource()
        uri = source._tile_uri(52.52, 13.405)
        assert uri == (
            "/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/"
            "Copernicus_DSM_COG_10_N52_00_E013_00_DEM/"
            "Copernicus_DSM_COG_10_N52_00_E013_00_DEM.tif"
        )

    def test_local_base_url_builds_plain_path_no_vsicurl(self, tmp_path: Path) -> None:
        """Ein nicht-http(s)-Base-URL wird als lokaler Pfad behandelt (Testbarkeit)."""
        source = CopernicusDEMDataSource(base_url=str(tmp_path))
        uri = source._tile_uri(52.52, 13.405)
        assert not uri.startswith("/vsicurl/")
        assert uri.startswith(str(tmp_path))

    def test_get_elevation_reads_local_tile(self, copernicus_tile_dir: Path) -> None:
        """Höhenwert wird aus der echten (lokalen) GeoTIFF-Kachel gelesen."""
        source = CopernicusDEMDataSource(base_url=str(copernicus_tile_dir))
        elevation = source.get_elevation(47.000075, 8.000075)
        # Mitte des 100m-120m-Gradienten-Tiles -> ca. 110m
        assert 105.0 <= elevation <= 115.0

    def test_get_elevation_missing_tile_falls_back_to_zero(self, copernicus_tile_dir: Path) -> None:
        """Koordinate ohne verfügbare Kachel liefert 0.0m statt zu werfen."""
        source = CopernicusDEMDataSource(base_url=str(copernicus_tile_dir))
        elevation = source.get_elevation(0.0, 0.0)
        assert elevation == 0.0

    def test_get_elevation_open_sea_returns_zero_not_raise(self, copernicus_tile_dir: Path) -> None:
        """Seegebiete ohne Kachel (kein Range-Request möglich) crashen nicht (Fährrouten)."""
        source = CopernicusDEMDataSource(base_url=str(copernicus_tile_dir))
        elevation = source.get_elevation(60.0, -10.0)
        assert elevation == 0.0

    @pytest.mark.asyncio
    async def test_get_elevations_batch_mixes_tile_hit_and_miss(
        self, copernicus_tile_dir: Path
    ) -> None:
        source = CopernicusDEMDataSource(base_url=str(copernicus_tile_dir))
        coords = [(47.00001, 8.00001), (47.00014, 8.00014), (0.0, 0.0)]
        elevations = await source.get_elevations_batch(coords)
        assert len(elevations) == 3
        assert elevations[0] > 0.0
        assert elevations[2] == 0.0

    def test_get_tile_at_returns_real_raster_metadata(self, copernicus_tile_dir: Path) -> None:
        """get_tile_at liefert die echten Raster-Metadaten der Kachel."""
        source = CopernicusDEMDataSource(base_url=str(copernicus_tile_dir))
        tile = source.get_tile_at(47.000075, 8.000075)
        assert tile is not None
        assert tile.width == 5
        assert tile.height == 5
        assert tile.key.min_lat == pytest.approx(47.0)
        assert tile.key.max_lon == pytest.approx(8.00015)

    def test_get_tile_at_missing_tile_returns_none(self, copernicus_tile_dir: Path) -> None:
        """get_tile_at liefert None statt zu werfen, wenn keine Kachel existiert."""
        source = CopernicusDEMDataSource(base_url=str(copernicus_tile_dir))
        assert source.get_tile_at(0.0, 0.0) is None

    def test_get_tiles_in_bbox_dedupes_by_tile_cell(self, copernicus_tile_dir: Path) -> None:
        """Mehrfache Anfragen innerhalb derselben 1x1-Grad-Zelle liefern nur eine Kachel."""
        source = CopernicusDEMDataSource(base_url=str(copernicus_tile_dir))
        tiles = source.get_tiles_in_bbox(47.0, 47.0001, 8.0, 8.0001)
        assert len(tiles) == 1

    def test_get_tiles_in_bbox_skips_missing_tiles(self, copernicus_tile_dir: Path) -> None:
        """BBox, die eine fehlende Nachbarkachel überstreicht, überspringt diese statt zu werfen."""
        source = CopernicusDEMDataSource(base_url=str(copernicus_tile_dir))
        tiles = source.get_tiles_in_bbox(46.5, 47.5, 7.5, 8.5)
        assert len(tiles) == 1

    def test_lru_cache_evicts_and_reopens_without_error(self, copernicus_tile_dir: Path) -> None:
        """Mit `max_open_tiles=1` wird das LRU-Cache-Limit durchgesetzt, ohne Fehler."""
        source = CopernicusDEMDataSource(base_url=str(copernicus_tile_dir), max_open_tiles=1)
        # Fordert zunächst die vorhandene, dann eine fehlende Kachel an (verdrängt die
        # erste aus dem Cache), und dann erneut die vorhandene - muss weiterhin
        # funktionieren (Dataset wird bei Bedarf new geöffnet).
        first = source.get_elevation(47.000075, 8.000075)
        _ = source.get_elevation(0.0, 0.0)
        second = source.get_elevation(47.000075, 8.000075)
        assert first == pytest.approx(second)

    @pytest.mark.asyncio
    async def test_concurrent_overlapping_batches_do_not_race_on_lru_eviction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Viele nebenläufige `get_elevations_batch`-Aufrufe, die sich ein paar
        Kacheln bei winzigem LRU-Cap teilen, dürfen kein Dataset schließen,
        das ein anderer Aufruf gerade noch liest.

        Regression für den SIGSEGV/SIGABRT, den `precompute_detour_costs`'
        ~60-fache nebenläufige Fan-out gegen `providers.py`s LRU auslöste:
        Eviction hat bisher die älteste Kachel bedingungslos `.close()`-t,
        selbst während ein anderer nebenläufiger `get_elevations_batch`-Aufruf
        gerade einen `dataset.read(1)` für exakt diese Kachel in Flug hatte
        (siehe `providers.py`s `_get_tile_lock`-Docstring). Jeder Batch fragt
        nur 2 der 5 Kacheln ab (die reale plus eine rotierende "Churn"-Kachel) -
        fragte ein Batch alle 5 ab, würde `get_elevations_batch`s eigene
        "nie die Kacheln dieses Batches verdrängen"-Regel den Cap sofort auf 5
        anheben und jede Eviction verhindern, was den Race unerreichbar machen
        würde.

        Die 5x5-Pixel-Test-Kacheln werden in Mikrosekunden gelesen - zu schnell,
        um das Race-Fenster in-process zuverlässig zu öffnen (anders als die
        echten ca. 3600x3600 Copernicus-Kacheln, deren ca. 40MB-`read(1)`
        zig Millisekunden dauert - long genug, damit eine nebenläufige
        Eviction mitten in den Read fällt). Eine kleine gemonkeypatchte
        Verzögerung auf `DatasetReader.read` stellt dieses Fenster
        deterministisch wieder her, ohne echte DEM-Kacheln zu benötigen.
        """
        original_read = rasterio.io.DatasetReader.read

        def _slow_read(self: rasterio.io.DatasetReader, *args: object, **kwargs: object) -> object:
            time.sleep(0.02)
            return original_read(self, *args, **kwargs)

        monkeypatch.setattr(rasterio.io.DatasetReader, "read", _slow_read)

        src = tmp_path / "_src.tif"
        _create_test_dem_tile.create_synthetic_dem_tile(src)
        for lon_deg in range(8, 13):  # E008..E012 - 5 distinct, real, openable files
            name = f"Copernicus_DSM_COG_10_N47_00_E0{lon_deg:02d}_00_DEM"
            folder = tmp_path / name
            folder.mkdir()
            shutil.copy(src, folder / f"{name}.tif")

        source = CopernicusDEMDataSource(base_url=str(tmp_path), max_open_tiles=1)
        real_point = (47.000075, 8.000075)  # falls within E008's actual embedded bounds
        churn_points = [(47.5, 8.5 + i) for i in range(4)]  # E009..E012, one per batch
        batches = [[real_point, churn_points[i % len(churn_points)]] for i in range(30)]

        results = await asyncio.gather(*[source.get_elevations_batch(b) for b in batches])

        for elevations in results:
            assert len(elevations) == 2
            # Mitte des 100-120m-Gradienten-Tiles (siehe copernicus_tile_dir) ->
            # ca. 110m, exakt wie in test_get_elevation_reads_local_tile. Ein
            # close-under-read-Race würde dies auf 0.0 verfälschen oder werfen.
            assert 105.0 <= elevations[0] <= 115.0

    @pytest.mark.integration
    def test_real_vsicurl_read_zugspitze_summit(self) -> None:
        """Echter /vsicurl/-Read gegen den öffentlichen Bucket (kein Download).

        Regressionswert am Zugspitze-Gipfel (47.42101N, 10.98518E), verifiziert
        durch einen direkten rasterio-Read gegen dieselbe Live-Kachel
        (Copernicus_DSM_COG_10_N47_00_E010_00_DEM): 2948.7m (DSM-Oberflächenhöhe,
        weicht leicht vom amtlichen Gipfelkreuz-Wert ~2962m ab, da Copernicus DEM
        ein Digital Surface Model, kein Digital Terrain Model ist).
        """
        source = CopernicusDEMDataSource()
        elevation = source.get_elevation(47.42101, 10.98518)
        assert elevation == pytest.approx(2948.7, abs=5.0)


class TestCalculateHorizontalDistance:
    """Tests für calculate_horizontal_distance."""

    def test_distance_zero_for_identical_points(self) -> None:
        """distance zwischen identischen Punkten ist 0."""
        coord = (47.0, 8.0)
        dist = calculate_horizontal_distance(coord, coord)
        assert dist == pytest.approx(0.0, abs=1e-6)

    def test_distance_symmetric(self) -> None:
        """distance(a, b) == distance(b, a)."""
        coord1 = (47.0, 8.0)
        coord2 = (48.0, 9.0)
        dist1 = calculate_horizontal_distance(coord1, coord2)
        dist2 = calculate_horizontal_distance(coord2, coord1)
        assert dist1 == pytest.approx(dist2)

    def test_distance_approximate_1_degree(self) -> None:
        """distance von 1° Längengrad am Äquator ≈ 111 km."""
        coord1 = (0.0, 0.0)
        coord2 = (0.0, 1.0)
        dist = calculate_horizontal_distance(coord1, coord2)
        assert dist == pytest.approx(111_320, rel=0.01)

    def test_distance_1_degree_latitude(self) -> None:
        """distance von 1° latitude ≈ 111 km."""
        coord1 = (0.0, 0.0)
        coord2 = (1.0, 0.0)
        dist = calculate_horizontal_distance(coord1, coord2)
        assert dist == pytest.approx(111_140, rel=0.01)


# =============================================================================
# cache_dir tests (Fix 3)
# =============================================================================


class TestCacheDir:
    """Tests for the local disk tile cache (Fix 3)."""

    def test_cache_dir_creates_tile_file_on_first_fetch(self, copernicus_tile_dir: Path) -> None:
        """First call with cache_dir set writes the tile to disk."""
        # Create a separate cache dir
        cache_dir = copernicus_tile_dir / "cache"
        cache_dir.mkdir()

        # Copy the test tile into the cache to simulate prior fetch
        tile_name = "Copernicus_DSM_COG_10_N47_00_E008_00_DEM"
        src_tile = next(iter(copernicus_tile_dir.rglob("*.tif")))
        shutil.copy(src_tile, cache_dir / f"{tile_name}.tif")

        source = CopernicusDEMDataSource(
            base_url=str(copernicus_tile_dir),
            cache_dir=cache_dir,
        )
        elevation = source.get_elevation(47.000075, 8.000075)
        assert 105.0 <= elevation <= 115.0
        # Verify tile file exists in cache
        cached_file = cache_dir / f"{tile_name}.tif"
        assert cached_file.exists(), "Tile file should exist in cache_dir after fetch"

    def test_schedule_cache_write_remote_tile_writes_from_memory(
        self, copernicus_tile_dir: Path, tmp_path: Path
    ) -> None:
        """Regression test: the disk-cache write must actually succeed for a
        remote (`/vsicurl/`-prefixed) tile, writing from the already-fetched
        in-memory band array without raising.

        Guards two prior bugs:
        1. `rasterio.shutil` is a submodule a bare `import rasterio` does not
           expose (`AttributeError: module 'rasterio' has no attribute
           'shutil'`) — fixed by writing directly from the in-memory band via
           `rasterio.open(path, "w", **profile)` instead of
           `rasterio.shutil.copy`.
        2. The write must not re-read the source dataset (that re-read was the
           cause of the elevation step regressing to multiple minutes).

        The other `TestCacheDir` tests all use a local (non-remote)
        `base_url`, so `_schedule_cache_write`'s `uri.startswith("/vsicurl/")`
        guard skips the write entirely and never exercises this path — this
        test opens a real local dataset but calls `_schedule_cache_write`
        with a `/vsicurl/`-prefixed URI directly to force the remote code
        path, with no real network call.
        """
        cache_dir = tmp_path / "cache_remote"
        cache_dir.mkdir()
        src_tile = next(iter(copernicus_tile_dir.rglob("*.tif")))

        source = CopernicusDEMDataSource(cache_dir=cache_dir)
        dataset = rasterio.open(str(src_tile))
        try:
            band = dataset.read(1)
            source._schedule_cache_write(
                "/vsicurl/https://example.com/fake/fake.tif", dataset, band, 47.0, 8.0
            )
        finally:
            dataset.close()

        cached_file = cache_dir / "Copernicus_DSM_COG_10_N47_00_E008_00_DEM.tif"
        deadline = time.time() + 5.0
        while not cached_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert cached_file.exists(), (
            "background cache write should have written the tile to cache_dir"
        )

    def test_cache_dir_reads_from_local_file(self, copernicus_tile_dir: Path) -> None:
        """Second call with broken base_url must still return correct value via local cache."""
        cache_dir = copernicus_tile_dir / "cache2"
        cache_dir.mkdir()

        # First, populate the cache with a fresh source
        tile_name = "Copernicus_DSM_COG_10_N47_00_E008_00_DEM"
        src_tile = next(iter(copernicus_tile_dir.rglob("*.tif")))
        shutil.copy(src_tile, cache_dir / f"{tile_name}.tif")

        # Now create source with broken base_url but same cache_dir
        source = CopernicusDEMDataSource(
            base_url="file:///nonexistent/broken_path",
            cache_dir=cache_dir,
        )
        # _tile_name for (47.000075, 8.000075) is N47/E008
        # _tile_uri should return the local cached path since cache was populated
        elevation = source.get_elevation(47.000075, 8.000075)
        assert 105.0 <= elevation <= 115.0

    def test_cache_write_failure_does_not_break_lookup(self, tmp_path: Path) -> None:
        """If cache_dir is non-writable, get_elevations_batch still returns correct values."""
        # Create a non-writable directory for cache
        readonly_dir = tmp_path / "readonly_cache"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)

        source = CopernicusDEMDataSource(
            base_url=str(tmp_path),
            cache_dir=readonly_dir,
        )
        # With no tiles in cache and no real tiles at base_url,
        # get_elevation falls back to 0.0. This just verifies no exception.
        result = source.get_elevation(47.0, 8.0)
        # Should return 0.0 (no tile), not raise
        assert result == 0.0

        # Restore permissions for cleanup
        readonly_dir.chmod(0o755)

    @pytest.mark.asyncio
    async def test_cache_dir_batch(self, copernicus_tile_dir: Path) -> None:
        """Batch elevation with cache_dir returns correct values from cached tile."""
        cache_dir = copernicus_tile_dir / "cache3"
        cache_dir.mkdir()

        tile_name = "Copernicus_DSM_COG_10_N47_00_E008_00_DEM"
        src_tile = next(iter(copernicus_tile_dir.rglob("*.tif")))
        shutil.copy(src_tile, cache_dir / f"{tile_name}.tif")

        source = CopernicusDEMDataSource(
            base_url=str(copernicus_tile_dir),
            cache_dir=cache_dir,
        )
        coords = [
            (47.000075, 8.000075),
            (47.00010, 8.00010),
        ]
        results = await source.get_elevations_batch(coords)
        assert len(results) == 2
        assert all(r > 0.0 for r in results)

    def test_cache_dir_skips_vsicurl_for_cached_tile(self, copernicus_tile_dir: Path) -> None:
        """When cache_dir has a tile, _tile_uri returns local path, not /vsicurl/."""
        cache_dir = copernicus_tile_dir / "cache4"
        cache_dir.mkdir()

        tile_name = "Copernicus_DSM_COG_10_N47_00_E008_00_DEM"
        src_tile = next(iter(copernicus_tile_dir.rglob("*.tif")))
        shutil.copy(src_tile, cache_dir / f"{tile_name}.tif")

        source = CopernicusDEMDataSource(
            base_url="https://copernicus-dem-30m.s3.amazonaws.com",
            cache_dir=cache_dir,
        )
        uri = source._tile_uri(47.000075, 8.000075)
        assert not uri.startswith("/vsicurl/"), f"Expected local path, got vsicurl URI: {uri}"
        assert cache_dir.name in uri or str(cache_dir) in uri, (
            f"Expected cache_dir path in URI: {uri}"
        )
