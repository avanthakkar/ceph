import time
import pytest

import cephutil
import smbutil


def _update_qos(smb_cfg, cluster_id, share_id, **qos_params):
    """Update QoS settings for a share using the CLI command."""
    cmd = [
        "ceph",
        "smb",
        "share",
        "update",
        "cephfs",
        "qos",
        "--cluster-id",
        cluster_id,
        "--share-id",
        share_id,
    ]

    for param, value in qos_params.items():
        if value is not None:
            cmd.extend([f"--{param.replace('_', '-')}", str(value)])

    jres = cephutil.cephadm_shell_cmd(
        smb_cfg,
        cmd,
        load_json=True,
    )

    assert (
        jres.returncode == 0
    ), f"Command failed with return code {jres.returncode}"
    assert jres.obj, "No JSON response from command"
    assert jres.obj.get("success"), f"Command not successful: {jres.obj}"

    # The response structure is: {"resource": {...}, "state": "...", "success": true}
    # NOT {"results": [{"resource": {...}}]}
    assert "resource" in jres.obj, f"Response missing 'resource' key: {jres.obj}"

    time.sleep(60)  # Allow settings to propagate

    # Return the updated share resource directly from the response
    return jres.obj["resource"]


def _test_transfer_rate(share_conn, filename, data_size_mb=1, operation="write"):
    """Test transfer rate by writing/reading a file and measuring time."""
    data = b"X" * (data_size_mb * 1024 * 1024)  # Create test data

    start_time = time.time()

    if operation == "write":
        # Create file path and write binary data using write_bytes
        file_path = share_conn / filename
        file_path.write_bytes(data)
    else:
        # Create file path and read binary data
        file_path = share_conn / filename
        with file_path.open("rb") as f:
            _ = f.read()

    end_time = time.time()
    duration = end_time - start_time

    data_size_mbits = data_size_mb * 8
    actual_rate_mbps = data_size_mbits / duration

    return duration, actual_rate_mbps


@pytest.mark.rate_limiting
class TestSMBRateLimiting:
    @pytest.fixture(scope="class")
    def config(self, smb_cfg):
        """Setup initial configuration with a test file."""
        orig = smbutil.get_shares(smb_cfg)[0]
        share_name = orig["name"]
        cluster_id = orig["cluster_id"]
        share_id = orig["share_id"]

        # Store original QoS settings if any
        original_qos = None
        if "cephfs" in orig and "qos" in orig["cephfs"]:
            original_qos = orig["cephfs"]["qos"]

        # Create a test file
        test_filename = "test_ratelimit_data.bin"
        print(f"Setting up test file for rate limiting tests on share {share_name}...")
        with smbutil.connection(smb_cfg, share_name) as sharep:
            # Create a 1MB file for initial testing
            test_data = b"X" * (1 * 1024 * 1024)
            file_path = sharep / test_filename
            file_path.write_bytes(test_data)

        yield {
            "share_name": share_name,
            "cluster_id": cluster_id,
            "share_id": share_id,
            "test_filename": test_filename,
            "original_share": orig,
            "original_qos": original_qos,
        }

        print("Restoring original share configuration...")
        smbutil.apply_share_config(smb_cfg, orig)

        with smbutil.connection(smb_cfg, share_name) as sharep:
            (sharep / test_filename).unlink()

    def test_qos_read_iops_limit(self, smb_cfg, config):
        """Test read IOPS rate limiting."""
        # Apply a read IOPS limit
        updated_share = _update_qos(
            smb_cfg, config["cluster_id"], config["share_id"], read_iops_limit=100
        )

        # Verify the QoS settings were applied correctly
        assert updated_share is not None
        assert updated_share["cluster_id"] == config["cluster_id"]
        assert updated_share["share_id"] == config["share_id"]
        assert "cephfs" in updated_share
        assert "qos" in updated_share["cephfs"]
        assert updated_share["cephfs"]["qos"]["read_iops_limit"] == 100

        # Verify with show command
        show_share = smbutil.get_share_by_id(
            smb_cfg, config["cluster_id"], config["share_id"]
        )
        assert show_share["cephfs"]["qos"]["read_iops_limit"] == 100

        print(f"✓ Read IOPS limit of 100 applied successfully to {config['share_id']}")

    def test_qos_write_iops_limit(self, smb_cfg, config):
        """Test write IOPS rate limiting."""
        # Apply a write IOPS limit
        updated_share = _update_qos(
            smb_cfg, config["cluster_id"], config["share_id"], write_iops_limit=50
        )

        # Verify the QoS settings were applied
        assert updated_share is not None
        assert updated_share["cephfs"]["qos"]["write_iops_limit"] == 50

        # Verify with show command
        show_share = smbutil.get_share_by_id(
            smb_cfg, config["cluster_id"], config["share_id"]
        )
        assert show_share["cephfs"]["qos"]["write_iops_limit"] == 50

        print(f"✓ Write IOPS limit of 50 applied successfully to {config['share_id']}")

    def test_qos_read_bandwidth_limit(self, smb_cfg, config):
        """Test read bandwidth rate limiting."""
        # Apply a read bandwidth limit (1 MB/s = 1048576 bytes/s)
        read_bw_limit = 1048576

        updated_share = _update_qos(
            smb_cfg,
            config["cluster_id"],
            config["share_id"],
            read_bw_limit=read_bw_limit,
        )

        # Verify the QoS settings were applied
        assert updated_share["cephfs"]["qos"]["read_bw_limit"] == read_bw_limit

        # Verify with show command
        show_share = smbutil.get_share_by_id(
            smb_cfg, config["cluster_id"], config["share_id"]
        )
        assert show_share["cephfs"]["qos"]["read_bw_limit"] == read_bw_limit

        print(f"✓ Read bandwidth limit of {read_bw_limit} bytes/s applied successfully")

    def test_qos_write_bandwidth_limit(self, smb_cfg, config):
        """Test write bandwidth rate limiting."""
        # Apply a write bandwidth limit (2 MB/s = 2097152 bytes/s)
        write_bw_limit = 2097152

        updated_share = _update_qos(
            smb_cfg,
            config["cluster_id"],
            config["share_id"],
            write_bw_limit=write_bw_limit,
        )

        # Verify the QoS settings were applied
        assert updated_share["cephfs"]["qos"]["write_bw_limit"] == write_bw_limit

        # Verify with show command
        show_share = smbutil.get_share_by_id(
            smb_cfg, config["cluster_id"], config["share_id"]
        )
        assert show_share["cephfs"]["qos"]["write_bw_limit"] == write_bw_limit

        print(f"✓ Write bandwidth limit of {write_bw_limit} bytes/s applied successfully")

    def test_qos_delay_max(self, smb_cfg, config):
        """Test delay_max settings."""
        # Apply delay_max settings
        read_delay_max = 100
        write_delay_max = 150
        updated_share = _update_qos(
            smb_cfg,
            config["cluster_id"],
            config["share_id"],
            read_delay_max=read_delay_max,
            write_delay_max=write_delay_max,
        )

        # Verify the QoS settings were applied
        qos = updated_share["cephfs"]["qos"]
        assert qos["read_delay_max"] == read_delay_max
        assert qos["write_delay_max"] == write_delay_max

        # Verify with show command
        show_share = smbutil.get_share_by_id(
            smb_cfg, config["cluster_id"], config["share_id"]
        )
        assert show_share["cephfs"]["qos"]["read_delay_max"] == read_delay_max
        assert show_share["cephfs"]["qos"]["write_delay_max"] == write_delay_max

        print(
            f"✓ Delay max settings applied successfully: read={read_delay_max}ms,"
            f" write={write_delay_max}ms"
        )

    def test_qos_multiple_limits(self, smb_cfg, config):
        """Test applying multiple QoS limits simultaneously."""
        # Apply multiple QoS limits
        updated_share = _update_qos(
            smb_cfg,
            config["cluster_id"],
            config["share_id"],
            read_iops_limit=100,
            write_iops_limit=50,
            read_bw_limit=4194304,
            write_bw_limit=2097152,
            read_delay_max=100,
            write_delay_max=150,
        )

        # Verify all QoS settings were applied
        qos = updated_share["cephfs"]["qos"]
        assert qos["read_iops_limit"] == 100
        assert qos["write_iops_limit"] == 50
        assert qos["read_bw_limit"] == 4194304
        assert qos["write_bw_limit"] == 2097152
        assert qos["read_delay_max"] == 100
        assert qos["write_delay_max"] == 150

        print("✓ Multiple QoS limits applied successfully")

    def test_qos_update_existing(self, smb_cfg, config):
        """Test updating existing QoS settings."""
        # First apply some QoS settings
        _update_qos(
            smb_cfg,
            config["cluster_id"],
            config["share_id"],
            read_iops_limit=100,
            read_bw_limit=1048576,
        )

        # Update with new values
        updated_share = _update_qos(
            smb_cfg,
            config["cluster_id"],
            config["share_id"],
            read_iops_limit=200,
            write_iops_limit=100,
            read_bw_limit=2097152,
        )

        # Verify updated values
        qos = updated_share["cephfs"]["qos"]
        assert qos["read_iops_limit"] == 200
        assert qos["write_iops_limit"] == 100
        assert qos["read_bw_limit"] == 2097152
        # write_bw_limit should not be set since we didn't specify it
        assert "write_bw_limit" not in qos

        print("✓ QoS update functionality working correctly")

    def test_qos_limits_clamping(self, smb_cfg, config):
        """Test that QoS limits are properly clamped to maximum values."""
        # Try to set values above the maximum limits
        excessive_iops = 2_000_000  # Above IOPS_LIMIT_MAX = 1,000,000
        excessive_bw = 2 << 40  # Above BYTES_LIMIT_MAX = 1 << 40 (1 TB)
        excessive_delay = 500  # Above DELAY_MAX_LIMIT = 300

        updated_share = _update_qos(
            smb_cfg,
            config["cluster_id"],
            config["share_id"],
            read_iops_limit=excessive_iops,
            write_iops_limit=excessive_iops,
            read_bw_limit=excessive_bw,
            write_bw_limit=excessive_bw,
            read_delay_max=excessive_delay,
            write_delay_max=excessive_delay,
        )

        # Verify values were clamped to maximums
        qos = updated_share["cephfs"]["qos"]
        assert qos["read_iops_limit"] == 1_000_000  # IOPS_LIMIT_MAX
        assert qos["write_iops_limit"] == 1_000_000  # IOPS_LIMIT_MAX
        assert qos["read_bw_limit"] == 1 << 40  # BYTES_LIMIT_MAX (1 TB)
        assert qos["write_bw_limit"] == 1 << 40  # BYTES_LIMIT_MAX (1 TB)
        assert qos["read_delay_max"] == 300  # DELAY_MAX_LIMIT
        assert qos["write_delay_max"] == 300  # DELAY_MAX_LIMIT

        print("✓ QoS limits properly clamped to maximum values")

    def test_qos_zero_values(self, smb_cfg, config):
        """Test that zero values are handled correctly (should be treated as no limit)."""
        # Apply QoS with zero values
        updated_share = _update_qos(
            smb_cfg,
            config["cluster_id"],
            config["share_id"],
            read_iops_limit=0,
            write_iops_limit=0,
            read_bw_limit=0,
            write_bw_limit=0,
            read_delay_max=0,
            write_delay_max=0,
        )

        assert "qos" not in updated_share["cephfs"]
        print("✓ Zero QoS values handled correctly")

    def test_qos_apply_via_resources(self, smb_cfg, config):
        """Test applying QoS settings via the apply command with resources JSON."""
        # Create a share resource with QoS settings
        share_resource = {
            "resource_type": "ceph.smb.share",
            "cluster_id": config["cluster_id"],
            "share_id": config["share_id"],
            "intent": "present",
            "name": config["original_share"]["name"],
            "readonly": False,
            "browseable": True,
            "cephfs": {
                "volume": config["original_share"]["cephfs"]["volume"],
                "path": config["original_share"]["cephfs"]["path"],
                "subvolume": config["original_share"]["cephfs"].get("subvolume", ""),
                "provider": config["original_share"]["cephfs"].get(
                    "provider", "samba-vfs"
                ),
                "qos": {
                    "read_iops_limit": 300,
                    "write_iops_limit": 150,
                    "read_bw_limit": 3145728,  # 3 MB/s
                    "write_bw_limit": 1572864,  # 1.5 MB/s
                    "read_delay_max": 50,
                    "write_delay_max": 75,
                },
            },
        }

        # Apply via resources
        updated_share = smbutil.apply_share_config(smb_cfg, share_resource)

        # Verify all QoS settings were applied
        qos = updated_share["cephfs"]["qos"]
        assert qos["read_iops_limit"] == 300
        assert qos["write_iops_limit"] == 150
        assert qos["read_bw_limit"] == 3145728
        assert qos["write_bw_limit"] == 1572864
        assert qos["read_delay_max"] == 50
        assert qos["write_delay_max"] == 75

        print("✓ QoS applied successfully via resources JSON")


# Performance-oriented tests
class TestSMBRateLimitingPerformance:
    @pytest.fixture(scope="class")
    def perf_config(self, smb_cfg):
        """Setup for performance tests."""
        orig = smbutil.get_shares(smb_cfg)[0]
        share_name = orig["name"]
        cluster_id = orig["cluster_id"]
        share_id = orig["share_id"]

        perf_filename = "perf_test_data.bin"
        print(f"Setting up performance test file on share {share_name}...")
        with smbutil.connection(smb_cfg, share_name) as sharep:
            # Create a 10MB file for performance testing
            test_data = b"X" * (10 * 1024 * 1024)
            file_path = sharep / perf_filename
            file_path.write_bytes(test_data)

        yield {
            "share_name": share_name,
            "cluster_id": cluster_id,
            "share_id": share_id,
            "perf_filename": perf_filename,
            "original_share": orig,
        }

        # Cleanup
        print("Cleaning up performance test files...")
        with smbutil.connection(smb_cfg, share_name) as sharep:
            (sharep / perf_filename).unlink()
        smbutil.apply_share_config(smb_cfg, orig)

    def test_bandwidth_limiting_effect(self, smb_cfg, perf_config):
        """Test that bandwidth limiting actually affects transfer speeds."""
        # Apply a low bandwidth limit (512 KB/s = 4194304 bits/s = 524288 bytes/s)
        low_bw_limit = 524288  # 512 KB/s

        _update_qos(
            smb_cfg,
            perf_config["cluster_id"],
            perf_config["share_id"],
            write_bw_limit=low_bw_limit,
        )

        # Test write performance with the limit
        test_filename = "bw_limit_test.bin"
        with smbutil.connection(smb_cfg, perf_config["share_name"]) as sharep:
            duration, actual_rate = _test_transfer_rate(
                sharep, test_filename, data_size_mb=2, operation="write"
            )

            # Calculate expected minimum time for 2MB at 512KB/s limit
            expected_min_time = (2 * 1024 * 1024) / low_bw_limit  # 2MB / 512KB/s

            print(f"Write test with {low_bw_limit} bytes/s limit:")
            print(f"  Duration: {duration:.2f}s")
            print(f"  Actual rate: {actual_rate:.2f} Mbps")
            print(f"  Expected minimum time: {expected_min_time:.2f}s")

            # The actual duration should be at least the expected minimum time
            # Allow 20% tolerance for test variability
            assert (
                duration >= expected_min_time * 0.8
            ), f"Transfer too fast ({duration:.2f}s) for {low_bw_limit} bytes/s limit"

            (sharep / test_filename).unlink()

        print("✓ Bandwidth limiting seems to be working")
