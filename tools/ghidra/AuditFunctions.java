// Export selected functions for local comparison; never executes the program.
// @category DipBotAudit
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Function;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;

public class AuditFunctions extends GhidraScript {
    public void run() throws Exception {
        if (!"0f9da36f8a9f0b9908d69e00a7c7c3501efb8d9823246078063267dababd0f72".equals(currentProgram.getExecutableSHA256()))
            throw new IllegalArgumentException("Unexpected release hash");
        Path output = Path.of(getScriptArgs()[0]);
        Files.createDirectories(output);
        String[] names = {"gui_close", "gui_stop", "commercial_close", "autopair_error", "sweep_error", "optional_trader_close", "converter_slippage"};
        long[] addresses = {0x140f37a90L,0x140f56f10L,0x140922010L,0x14090fd90L,0x14091a140L,0x142093c20L,0x141e76010L};
        createLabel(toAddr(0x142a0d2e0L), "win_gui__balance_generation", true);
        createLabel(toAddr(0x142a0d360L), "win_gui_bot", true);
        createLabel(toAddr(0x142a0d3f0L), "win_gui__balance_timer", true);
        createLabel(toAddr(0x142a0daa0L), "win_gui_stop_bot_btn", true);
        createLabel(toAddr(0x142a0e0f8L), "win_gui_append_log", true);
        createLabel(toAddr(0x142a0e140L), "win_gui_running", true);
        createLabel(toAddr(0x142a0e230L), "win_gui__save_ui_state", true);
        createLabel(toAddr(0x142a0e378L), "win_gui_stop", true);
        createLabel(toAddr(0x142a0e380L), "win_gui_closeEvent", true);
        createLabel(toAddr(0x1429db3b0L), "win_commercial_features__pair_holdings_generation", true);
        createLabel(toAddr(0x1429db3c0L), "win_commercial_features__live_pair_generation", true);
        createLabel(toAddr(0x1429db578L), "win_commercial_features__autopair_generation", true);
        createLabel(toAddr(0x1429db580L), "win_commercial_features__autopair_inflight_generation", true);
        createLabel(toAddr(0x1429db5a0L), "win_commercial_features__autopair_pending_address", true);
        createLabel(toAddr(0x1429db5a8L), "win_commercial_features__autopair_effective_token", true);
        createLabel(toAddr(0x1429db5b0L), "win_commercial_features__autopair_last_result", true);
        createLabel(toAddr(0x1429db5c0L), "win_commercial_features__autopair_last_status", true);
        createLabel(toAddr(0x1429db608L), "win_commercial_features__autopair_timer", true);
        createLabel(toAddr(0x1429db690L), "win_commercial_features__set_autopair_status", true);
        createLabel(toAddr(0x1429db710L), "win_commercial_features_append_log", true);
        createLabel(toAddr(0x1429dbab0L), "win_commercial_features_get", true);
        createLabel(toAddr(0x1429dbc58L), "win_commercial_features_stop", true);
        createLabel(toAddr(0x1429dbe28L), "win_commercial_features_ERROR", true);
        createLabel(toAddr(0x1429dc048L), "win_commercial_features__finish_wallet_sweep_ui", true);
        createLabel(toAddr(0x1429dc238L), "win_commercial_features_closeEvent", true);
        createLabel(toAddr(0x142a70150L), "win_wallet_sweep_close", true);
        createLabel(toAddr(0x142a70158L), "win_wallet_sweep_callable", true);
        long[] ends = {0x140f37fb8L,0x140f571e1L,0x140922552L,0x140910779L,0x14091a54dL,0x142093f94L,0x141e76660L};
        DecompInterface decompiler = new DecompInterface();
        try {
            decompiler.openProgram(currentProgram);
            for (int i=0; i<addresses.length; i++) {
                Address address = toAddr(addresses[i]);
                Address end = toAddr(ends[i]-1);
                AddressSet scope = new AddressSet(address, end);
                for (Function old : currentProgram.getFunctionManager().getFunctions(scope, true))
                    removeFunction(old);
                clearListing(address, end);
                new DisassembleCommand(address, scope, true).applyTo(currentProgram, monitor);
                Function function = createFunction(address, names[i]);
                if (function == null) { println("AUDIT_FAILED " + names[i]); continue; }
                function.setBody(scope);
                StringBuilder listing = new StringBuilder();
                for (Instruction instruction : currentProgram.getListing().getInstructions(scope, true))
                    listing.append(instruction.getAddress()).append(" ").append(instruction.toString()).append("\n");
                Files.writeString(output.resolve(names[i]+".asm"), listing.toString(), StandardCharsets.UTF_8);
                decompiler.flushCache();
                DecompileResults result = decompiler.decompileFunction(function, 45, monitor);
                if (!result.decompileCompleted()) { println("AUDIT_FAILED " + names[i] + " " + result.getErrorMessage()); continue; }
                Files.writeString(output.resolve(names[i]+".c"), result.getDecompiledFunction().getC(), StandardCharsets.UTF_8);
                println("AUDIT_OK " + names[i] + " " + address + " body=" + function.getBody().getNumAddresses());
            }
        } finally { decompiler.dispose(); }
    }
}
