// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script} from "forge-std/Script.sol";
import {console} from "forge-std/console.sol";
import {FindingRegistry} from "../src/FindingRegistry.sol";

contract Deploy is Script {
    function run() external returns (FindingRegistry registry) {
        vm.startBroadcast();
        registry = new FindingRegistry();
        vm.stopBroadcast();

        console.log("FindingRegistry:", address(registry));
        console.log("owner:", registry.owner());
    }
}
