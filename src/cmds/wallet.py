import asyncio
import sys
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp
import click

from src.cmds.units import units
from src.rpc.wallet_rpc_client import WalletRpcClient
from src.server.start_wallet import SERVICE_NAME
from src.util.bech32m import encode_puzzle_hash
from src.util.byte_types import hexstr_to_bytes
from src.util.config import load_config
from src.util.default_root import DEFAULT_ROOT_PATH
from src.util.ints import uint16, uint64
from src.wallet.transaction_record import TransactionRecord
from src.wallet.util.wallet_types import WalletType


def print_transaction(tx: TransactionRecord, verbose: bool, name) -> None:
    if verbose:
        print(tx)
    else:
        chia_amount = Decimal(int(tx.amount)) / units["chia"]
        to_address = encode_puzzle_hash(tx.to_puzzle_hash, name)
        print(f"Transaction {tx.name}")
        print(f"Status: {'Confirmed' if tx.confirmed else ('In mempool' if tx.is_in_mempool() else 'Pending')}")
        print(f"Amount: {chia_amount} {name}")
        print(f"To address: {to_address}")
        print("Created at:", datetime.fromtimestamp(tx.created_at_time).strftime("%Y-%m-%d %H:%M:%S"))
        print("")


async def get_transaction(args: dict, wallet_client: WalletRpcClient, fingerprint: int) -> None:
    wallet_id = args["id"]
    transaction_id = hexstr_to_bytes(args["tx_id"])
    config = load_config(DEFAULT_ROOT_PATH, "config.yaml", SERVICE_NAME)
    name = config["network_overrides"]["config"][config["selected_network"]]["address_prefix"]
    tx: TransactionRecord = await wallet_client.get_transaction(wallet_id, transaction_id=transaction_id)
    print_transaction(tx, verbose=(args["verbose"] > 0), name=name)


async def get_transactions(args: dict, wallet_client: WalletRpcClient, fingerprint: int) -> None:
    wallet_id = args["id"]
    txs: List[TransactionRecord] = await wallet_client.get_transactions(wallet_id)
    config = load_config(DEFAULT_ROOT_PATH, "config.yaml", SERVICE_NAME)
    name = config["network_overrides"]["config"][config["selected_network"]]["address_prefix"]
    if len(txs) == 0:
        print("There are no transactions to this address")

    num_per_screen = 5
    for i in range(0, len(txs), num_per_screen):
        for j in range(0, num_per_screen):
            if i + j >= len(txs):
                break
            print_transaction(txs[i + j], verbose=(args["verbose"] > 0), name=name)
        if i + num_per_screen >= len(txs):
            return
        print("Press q to quit, or c to continue")
        while True:
            entered_key = sys.stdin.read(1)
            if entered_key == "q":
                return
            elif entered_key == "c":
                break


async def send(args: dict, wallet_client: WalletRpcClient, fingerprint: int) -> None:
    wallet_id = args["id"]
    amount = Decimal(args["amount"])
    fee = Decimal(args["fee"])
    address = args["address"]

    print("Submitting transaction...")
    final_amount = uint64(int(amount * units["chia"]))
    final_fee = uint64(int(fee * units["chia"]))
    res = await wallet_client.send_transaction(wallet_id, final_amount, address, final_fee)
    tx_id = res.name
    start = time.time()
    while time.time() - start < 10:
        await asyncio.sleep(0.1)
        tx = await wallet_client.get_transaction(wallet_id, tx_id)
        if len(tx.sent_to) > 0:
            print(f"Transaction submitted to nodes: {tx.sent_to}")
            print(f"Do chia wallet get_transaction -f {fingerprint} -tx 0x{tx_id} to get status")
            return

    print("Transaction not yet submitted to nodes")
    print(f"Do 'chia wallet get_transaction -f {fingerprint} -tx 0x{tx_id}' to get status")


async def get_address(args: dict, wallet_client: WalletRpcClient, fingerprint: int) -> None:
    wallet_id = args["id"]
    res = await wallet_client.get_next_address(wallet_id, False)
    print(res)


async def print_balances(args: dict, wallet_client: WalletRpcClient, fingerprint: int) -> None:
    summaries_response = await wallet_client.get_wallets()
    config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
    address_prefix = config["network_overrides"]["config"][config["selected_network"]]["address_prefix"]

    print(f"Wallet height: {await wallet_client.get_height_info()}")
    print(f"Balances, fingerprint: {fingerprint}")
    for summary in summaries_response:
        wallet_id = summary["id"]
        balances = await wallet_client.get_wallet_balance(wallet_id)
        typ = WalletType(int(summary["type"])).name
        if typ != "STANDARD_WALLET":
            print(f"Wallet ID {wallet_id} type {typ} {summary['name']}")
            print(f"   -Confirmed: " f"{balances['confirmed_wallet_balance']/units['colouredcoin']}")
            print(f"   -Unconfirmed: {balances['unconfirmed_wallet_balance']/units['colouredcoin']}")
            print(f"   -Spendable: {balances['spendable_balance']/units['colouredcoin']}")
            print(f"   -Pending change: {balances['pending_change']/units['colouredcoin']}")
        else:
            print(f"Wallet ID {wallet_id} type {typ}")
            print(
                f"   -Confirmed: {balances['confirmed_wallet_balance']} mojo "
                f"({balances['confirmed_wallet_balance']/units['chia']} {address_prefix})"
            )
            print(
                f"   -Unconfirmed: {balances['unconfirmed_wallet_balance']} mojo "
                f"({balances['unconfirmed_wallet_balance']/units['chia']} {address_prefix})"
            )
            print(
                f"   -Spendable: {balances['spendable_balance']} mojo "
                f"({balances['spendable_balance']/units['chia']} {address_prefix})"
            )
            print(
                f"   -Pending change: {balances['pending_change']} mojo "
                f"({balances['pending_change']/units['chia']} {address_prefix})"
            )


async def get_wallet(wallet_client: WalletRpcClient, fingerprint: int = None) -> Optional[Tuple[WalletRpcClient, int]]:
    fingerprints = await wallet_client.get_public_keys()
    if len(fingerprints) == 0:
        print("No keys loaded. Run 'chia keys generate' or import a key")
        return None
    if fingerprint is not None:
        if fingerprint not in fingerprints:
            print(f"Fingerprint {fingerprint} does not exist")
            return None
    if len(fingerprints) == 1:
        fingerprint = fingerprints[0]
    if fingerprint is not None:
        log_in_response = await wallet_client.log_in(fingerprint)
    else:
        print("Choose wallet key:")
        for i, fp in enumerate(fingerprints):
            print(f"{i+1}) {fp}")
        val = None
        while val is None:
            val = input("Enter a number to pick or q to quit: ")
            if val == "q":
                return None
            if not val.isdigit():
                val = None
            else:
                index = int(val) - 1
                if index >= len(fingerprints):
                    print("Invalid value")
                    val = None
                    continue
                else:
                    fingerprint = fingerprints[index]
        assert fingerprint is not None
        log_in_response = await wallet_client.log_in(fingerprint)

    if log_in_response["success"] is False:
        if log_in_response["error"] == "not_initialized":
            use_cloud = True
            if "backup_path" in log_in_response:
                path = log_in_response["backup_path"]
                print(f"Backup file from backup.chia.net downloaded and written to: {path}")
                val = input("Do you want to use this file to restore from backup? (Y/N) ")
                if val.lower() == "y":
                    log_in_response = await wallet_client.log_in_and_restore(fingerprint, path)
                else:
                    use_cloud = False

            if "backup_path" not in log_in_response or use_cloud is False:
                if use_cloud is True:
                    val = input(
                        "No online backup file found, \n Press S to skip restore from backup"
                        " \n Press F to use your own backup file: "
                    )
                else:
                    val = input(
                        "Cloud backup declined, \n Press S to skip restore from backup"
                        " \n Press F to use your own backup file: "
                    )

                if val.lower() == "s":
                    log_in_response = await wallet_client.log_in_and_skip(fingerprint)
                elif val.lower() == "f":
                    val = input("Please provide the full path to your backup file: ")
                    log_in_response = await wallet_client.log_in_and_restore(fingerprint, val)

    if "success" not in log_in_response or log_in_response["success"] is False:
        if "error" in log_in_response:
            error = log_in_response["error"]
            print(f"Error: {log_in_response[error]}")
        return None
    return wallet_client, fingerprint


async def execute_with_wallet(wallet_rpc_port: int, fingerprint: int, extra_params: dict, function: Callable) -> None:
    try:
        config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
        self_hostname = config["self_hostname"]
        if wallet_rpc_port is None:
            wallet_rpc_port = config["wallet"]["rpc_port"]
        wallet_client = await WalletRpcClient.create(self_hostname, uint16(wallet_rpc_port), DEFAULT_ROOT_PATH, config)
        wallet_client_f = await get_wallet(wallet_client, fingerprint=fingerprint)
        if wallet_client_f is None:
            wallet_client.close()
            await wallet_client.await_closed()
            return
        wallet_client, fingerprint = wallet_client_f
        await function(extra_params, wallet_client, fingerprint)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        if isinstance(e, aiohttp.client_exceptions.ClientConnectorError):
            print(f"Connection error. Check if wallet is running at {wallet_rpc_port}")
        else:
            print(f"Exception from 'wallet' {e}")
    wallet_client.close()
    await wallet_client.await_closed()


async def create_new_wallet(args: dict, wallet_client: WalletRpcClient, fingerprint: int) -> None:
    data = await wallet_client.create_new_wallet(args['wallet_type'], args['data'])
    if data and data['success']:
        if args['wallet_type'] == "cc_wallet" and args['mode'] == "new":
            print("New colour created: ", data['colour'])
        elif args['wallet_type'] == "cc_wallet" and args['mode'] == "existing":
            print("New colour wallet created.")
    else:
        print("Unable to create the new wallet")


@click.group("wallet", short_help="Manage your wallet")
def wallet_cmd() -> None:
    pass


@wallet_cmd.command("get_transaction", short_help="Get a transaction")
@click.option(
    "-wp",
    "--wallet-rpc-port",
    help="Set the port where the Wallet is hosting the RPC interface. See the rpc_port under wallet in config.yaml",
    type=int,
    default=9256,
    show_default=True,
)
@click.option("-f", "--fingerprint", help="Set the fingerprint to specify which wallet to use", type=int)
@click.option("-i", "--id", help="Id of the wallet to use", type=int, default=1, show_default=True, required=True)
@click.option("-tx", "--tx_id", help="transaction id to search for", type=str, required=True)
@click.option("--verbose", "-v", count=True, type=int)
def get_transaction_cmd(wallet_rpc_port: int, fingerprint: int, id: int, tx_id: str, verbose: int) -> None:
    extra_params = {"id": id, "tx_id": tx_id, "verbose": verbose}
    asyncio.run(execute_with_wallet(wallet_rpc_port, fingerprint, extra_params, get_transaction))


@wallet_cmd.command("get_transactions", short_help="Get all transactions")
@click.option(
    "-wp",
    "--wallet-rpc-port",
    help="Set the port where the Wallet is hosting the RPC interface. See the rpc_port under wallet in config.yaml",
    type=int,
    default=9256,
    show_default=True,
)
@click.option("-f", "--fingerprint", help="Set the fingerprint to specify which wallet to use", type=int)
@click.option("-i", "--id", help="Id of the wallet to use", type=int, default=1, show_default=True, required=True)
@click.option("--verbose", "-v", count=True, type=int)
def get_transactions_cmd(wallet_rpc_port: int, fingerprint: int, id: int, verbose: bool) -> None:
    extra_params = {"id": id, "verbose": verbose}
    asyncio.run(execute_with_wallet(wallet_rpc_port, fingerprint, extra_params, get_transactions))


@wallet_cmd.command("send", short_help="Send chia to another wallet")
@click.option(
    "-wp",
    "--wallet-rpc-port",
    help="Set the port where the Wallet is hosting the RPC interface. See the rpc_port under wallet in config.yaml",
    type=int,
    default=9256,
    show_default=True,
)
@click.option("-f", "--fingerprint", help="Set the fingerprint to specify which wallet to use", type=int)
@click.option("-i", "--id", help="Id of the wallet to use", type=int, default=1, show_default=True, required=True)
@click.option("-a", "--amount", help="How much chia to send, in TXCH/XCH", type=str, required=True)
@click.option(
    "-m", "--fee", help="Set the fees for the transaction", type=str, default="0", show_default=True, required=True
)
@click.option("-t", "--address", help="Address to send the TXCH/XCH", type=str, required=True)
def send_cmd(wallet_rpc_port: int, fingerprint: int, id: int, amount: str, fee: str, address: str) -> None:
    extra_params = {"id": id, "amount": amount, "fee": fee, "address": address}
    asyncio.run(execute_with_wallet(wallet_rpc_port, fingerprint, extra_params, send))


@wallet_cmd.command("show", short_help="Show wallet information")
@click.option(
    "-wp",
    "--wallet-rpc-port",
    help="Set the port where the Wallet is hosting the RPC interface. See the rpc_port under wallet in config.yaml",
    type=int,
    default=9256,
    show_default=True,
)
@click.option("-f", "--fingerprint", help="Set the fingerprint to specify which wallet to use", type=int)
def show_cmd(wallet_rpc_port: int, fingerprint: int) -> None:
    asyncio.run(execute_with_wallet(wallet_rpc_port, fingerprint, {}, print_balances))


@wallet_cmd.command("get_address", short_help="Get a wallet receive address")
@click.option(
    "-wp",
    "--wallet-rpc-port",
    help="Set the port where the Wallet is hosting the RPC interface. See the rpc_port under wallet in config.yaml",
    type=int,
    default=9256,
    show_default=True,
)
@click.option("-i", "--id", help="Id of the wallet to use", type=int, default=1, show_default=True, required=True)
@click.option("-f", "--fingerprint", help="Set the fingerprint to specify which wallet to use", type=int)
def get_address_cmd(wallet_rpc_port: int, id, fingerprint: int) -> None:
    extra_params = {"id": id}
    asyncio.run(execute_with_wallet(wallet_rpc_port, fingerprint, extra_params, get_address))


@wallet_cmd.group("create", short_help="Create new wallets")
def wallet_create_cmd():
    pass


@wallet_create_cmd.command("coloured-coin", short_help="Create a coloured coin wallet")
@click.option(
    "-wp",
    "--wallet-rpc-port",
    help="Set the port where the Wallet is hosting the RPC interface. See the rpc_port under wallet in config.yaml",
    type=int,
    default=9256,
    show_default=True,
)
@click.option("-c", "--colour-id", help="Id of the colour for the new wallet", type=str)
@click.option("-a", "--amount", help="How much chia to destroy to create this coloured coin, in TXCH/XCH", type=str)
@click.option(
    "-m", "--fee", help="Set the fees for the transaction", type=str, default="0", show_default=True, required=True
)
@click.option("-f", "--fingerprint", help="Set the fingerprint to specify which wallet to use", type=int)
def create_coloured_coin_cmd(wallet_rpc_port: int, colour_id: str, amount: str, fee: str, fingerprint: int) -> None:
    if not colour_id and not amount:
        print((
            "You must use --amount to create a new coloured coin or --colour-id "
            "to create a wallet of an existing colour, but at least one."
        ))
        sys.exit(1)
    if colour_id and amount:
        print((
            "You can use --amount to create a new coloured coin or --colour-id "
            "to create a wallet of an existing colour, but not both."
        ))
        sys.exit(1)

    final_fee = uint64(int(Decimal(fee) * units["chia"]))
    data: Dict[str, Any] = {"fee": final_fee}
    if colour_id:
        data['mode'] = "existing"
        data['colour'] = colour_id
    else:
        data['mode'] = "new"
        final_amount = uint64(int(Decimal(amount) * units["chia"]))
        data['amount'] = final_amount
    extra_params = {"wallet_type": "cc_wallet", "data": data}
    asyncio.run(execute_with_wallet(wallet_rpc_port, fingerprint, extra_params, create_new_wallet))
