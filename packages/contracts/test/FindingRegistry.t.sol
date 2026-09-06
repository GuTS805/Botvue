// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {FindingRegistry} from "../src/FindingRegistry.sol";

contract FindingRegistryTest is Test {
    FindingRegistry internal registry;

    address internal owner = address(this);
    address internal scanner = makeAddr("scanner");
    address internal publisher = makeAddr("publisher");
    address internal stranger = makeAddr("stranger");

    bytes32 internal constant DOMAIN = keccak256("example.com");
    bytes32 internal constant EVIDENCE = keccak256("machine-only blocks");

    event DivergenceRecorded(
        uint256 indexed findingId,
        bytes32 indexed domainHash,
        FindingRegistry.Classification indexed classification,
        string url,
        string userAgent,
        bytes32 evidenceHash,
        uint16 divergenceBps,
        uint64 observedAt,
        address reporter
    );

    event FindingDisputed(uint256 indexed findingId, address indexed disputer, string reason);

    function setUp() public {
        registry = new FindingRegistry();
    }

    function _record(address as_) internal returns (uint256) {
        vm.prank(as_);
        return registry.record(
            DOMAIN,
            "https://example.com/article",
            "ClaudeBot/1.0",
            FindingRegistry.Classification.PromptInjection,
            EVIDENCE,
            2500,
            uint64(block.timestamp)
        );
    }

    function test_deployerIsOwnerAndReporter() public view {
        assertEq(registry.owner(), owner);
        assertTrue(registry.isReporter(owner));
        assertEq(registry.findingCount(), 0);
    }

    function test_recordStoresAndEmits() public {
        vm.expectEmit(true, true, true, true);
        emit DivergenceRecorded(
            0,
            DOMAIN,
            FindingRegistry.Classification.PromptInjection,
            "https://example.com/article",
            "ClaudeBot/1.0",
            EVIDENCE,
            2500,
            uint64(block.timestamp),
            owner
        );

        uint256 id = _record(owner);

        assertEq(id, 0);
        assertEq(registry.findingCount(), 1);

        FindingRegistry.Finding memory f = registry.getFinding(id);
        assertEq(f.domainHash, DOMAIN);
        assertEq(f.evidenceHash, EVIDENCE);
        assertEq(f.reporter, owner);
        assertEq(f.divergenceBps, 2500);
        assertEq(uint8(f.classification), uint8(FindingRegistry.Classification.PromptInjection));
        assertFalse(f.disputed);
    }

    function test_findingIdsIncrement() public {
        assertEq(_record(owner), 0);
        assertEq(_record(owner), 1);
        assertEq(registry.findingCount(), 2);
    }

    function test_revertsWhenSenderIsNotReporter() public {
        vm.expectRevert(FindingRegistry.NotReporter.selector);
        _record(stranger);
    }

    function test_revertsOnDivergenceAboveFullRange() public {
        vm.expectRevert(FindingRegistry.InvalidDivergence.selector);
        registry.record(
            DOMAIN,
            "https://example.com/article",
            "ClaudeBot/1.0",
            FindingRegistry.Classification.Cosmetic,
            EVIDENCE,
            10_001,
            uint64(block.timestamp)
        );
    }

    function test_ownerCanAuthoriseAndRevokeReporter() public {
        registry.setReporter(scanner, true);
        assertTrue(registry.isReporter(scanner));
        assertEq(_record(scanner), 0);

        registry.setReporter(scanner, false);
        vm.expectRevert(FindingRegistry.NotReporter.selector);
        _record(scanner);
    }

    function test_setReporterRejectsNonOwnerAndZeroAddress() public {
        vm.prank(stranger);
        vm.expectRevert(FindingRegistry.NotOwner.selector);
        registry.setReporter(scanner, true);

        vm.expectRevert(FindingRegistry.ZeroAddress.selector);
        registry.setReporter(address(0), true);
    }

    /// A publisher must be able to answer a finding without being able to erase it.
    function test_anyoneCanDisputeAndRecordSurvives() public {
        uint256 id = _record(owner);

        vm.expectEmit(true, true, false, true);
        emit FindingDisputed(id, publisher, "served by an unrelated experiment");

        vm.prank(publisher);
        registry.dispute(id, "served by an unrelated experiment");

        FindingRegistry.Finding memory f = registry.getFinding(id);
        assertTrue(f.disputed);
        assertEq(f.evidenceHash, EVIDENCE);
        assertEq(registry.findingCount(), 1);
    }

    function test_revertsOnUnknownFinding() public {
        vm.expectRevert(FindingRegistry.UnknownFinding.selector);
        registry.getFinding(0);

        vm.expectRevert(FindingRegistry.UnknownFinding.selector);
        registry.dispute(0, "no such finding");
    }

    function test_ownershipTransfer() public {
        registry.transferOwnership(scanner);
        assertEq(registry.owner(), scanner);

        vm.expectRevert(FindingRegistry.NotOwner.selector);
        registry.setReporter(stranger, true);
    }

    function testFuzz_recordAcceptsFullDivergenceRange(uint16 bps) public {
        bps = uint16(bound(bps, 0, 10_000));
        registry.record(
            DOMAIN,
            "https://example.com/article",
            "GPTBot/1.1",
            FindingRegistry.Classification.Promotional,
            EVIDENCE,
            bps,
            uint64(block.timestamp)
        );
        assertEq(registry.getFinding(0).divergenceBps, bps);
    }
}
