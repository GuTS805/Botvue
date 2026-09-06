// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title FindingRegistry
/// @notice Append-only record of pages observed serving different content to AI crawlers
/// than to a browser.
/// @dev Publishers serve these responses with `cache-control: no-store`, so no cached copy
/// survives for anyone to check later. Anchoring the evidence hash on-chain is what lets an
/// observation still be verified after the page has been reverted. URL and user-agent are
/// emitted in the event only — the subgraph indexes them, and keeping them out of storage
/// holds a finding to three slots.
contract FindingRegistry {
    /// @dev MachineOnly covers substantive content served only to crawlers that carries no
    /// promotional or instructional marker. Recording it as advertising would assert an
    /// intent that cannot be observed from the response alone.
    enum Classification {
        Cosmetic,
        MachineOnly,
        Promotional,
        PolicyViolation,
        PromptInjection
    }

    struct Finding {
        bytes32 domainHash;
        bytes32 evidenceHash;
        address reporter;
        uint64 observedAt;
        uint16 divergenceBps;
        Classification classification;
        bool disputed;
    }

    event DivergenceRecorded(
        uint256 indexed findingId,
        bytes32 indexed domainHash,
        Classification indexed classification,
        string url,
        string userAgent,
        bytes32 evidenceHash,
        uint16 divergenceBps,
        uint64 observedAt,
        address reporter
    );

    /// @notice Anyone may dispute; the record is never deleted, only annotated. A publisher
    /// needs a route to answer a finding without the observation itself being erasable.
    event FindingDisputed(uint256 indexed findingId, address indexed disputer, string reason);

    event ReporterUpdated(address indexed reporter, bool allowed);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error NotReporter();
    error UnknownFinding();
    error ZeroAddress();
    error InvalidDivergence();

    address public owner;
    mapping(address => bool) public isReporter;

    Finding[] private _findings;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor() {
        owner = msg.sender;
        isReporter[msg.sender] = true;
        emit OwnershipTransferred(address(0), msg.sender);
        emit ReporterUpdated(msg.sender, true);
    }

    function record(
        bytes32 domainHash,
        string calldata url,
        string calldata userAgent,
        Classification classification,
        bytes32 evidenceHash,
        uint16 divergenceBps,
        uint64 observedAt
    ) external returns (uint256 findingId) {
        if (!isReporter[msg.sender]) revert NotReporter();
        if (divergenceBps > 10_000) revert InvalidDivergence();

        findingId = _findings.length;
        _findings.push(
            Finding({
                domainHash: domainHash,
                evidenceHash: evidenceHash,
                reporter: msg.sender,
                observedAt: observedAt,
                divergenceBps: divergenceBps,
                classification: classification,
                disputed: false
            })
        );

        emit DivergenceRecorded(
            findingId,
            domainHash,
            classification,
            url,
            userAgent,
            evidenceHash,
            divergenceBps,
            observedAt,
            msg.sender
        );
    }

    function dispute(uint256 findingId, string calldata reason) external {
        if (findingId >= _findings.length) revert UnknownFinding();
        _findings[findingId].disputed = true;
        emit FindingDisputed(findingId, msg.sender, reason);
    }

    function setReporter(address reporter, bool allowed) external onlyOwner {
        if (reporter == address(0)) revert ZeroAddress();
        isReporter[reporter] = allowed;
        emit ReporterUpdated(reporter, allowed);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function findingCount() external view returns (uint256) {
        return _findings.length;
    }

    function getFinding(uint256 findingId) external view returns (Finding memory) {
        if (findingId >= _findings.length) revert UnknownFinding();
        return _findings[findingId];
    }
}
