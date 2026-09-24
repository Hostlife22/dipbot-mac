// Apply manually inferred Windows x64 helper signatures to the audit project.
// @category DipBotAudit
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Parameter;
import ghidra.program.model.listing.ParameterImpl;
import ghidra.program.model.data.PointerDataType;
import ghidra.program.model.data.IntegerDataType;
import ghidra.program.model.data.BooleanDataType;
import ghidra.program.model.symbol.SourceType;

public class NuitkaPrototypes extends GhidraScript {
    public void run() throws Exception {
        if (!"0f9da36f8a9f0b9908d69e00a7c7c3501efb8d9823246078063267dababd0f72".equals(currentProgram.getExecutableSHA256()))
            throw new IllegalArgumentException("Unexpected release");
        long[] addresses={0x142911880L,0x1429119e0L,0x1429125c0L,0x1428fccf0L,0x1429128d0L,0x142912980L,0x142905d20L,0x142906280L};
        String[] names={"inferred_method_call0","inferred_method_call1","inferred_get_attribute","inferred_getattr_default","inferred_has_attribute","inferred_set_attribute","inferred_call0","inferred_call1"};
        String[][] args={{"tstate","receiver","name"},{"tstate","receiver","name","argument"},
                         {"unused_context","receiver","name"},{"tstate","receiver","name","fallback"},{"tstate","receiver","name"},
                         {"tstate","receiver","name","value"},{"tstate","callable"},{"tstate","callable","argument"}};
        for(int i=0;i<addresses.length;i++) {
            Function f=getFunctionAt(toAddr(addresses[i]));
            if(f==null) { disassemble(toAddr(addresses[i]));f=createFunction(toAddr(addresses[i]),names[i]); }
            if(f==null) throw new IllegalStateException(names[i]);
            f.setName(names[i],SourceType.USER_DEFINED);
            f.setReturnType(i==4 ? IntegerDataType.dataType : i==5 ? BooleanDataType.dataType : PointerDataType.dataType,SourceType.USER_DEFINED);
            Parameter[] parameters=new Parameter[args[i].length];
            for(int j=0;j<parameters.length;j++)
                parameters[j]=new ParameterImpl(args[i][j],PointerDataType.dataType,currentProgram);
            f.replaceParameters(Function.FunctionUpdateType.DYNAMIC_STORAGE_ALL_PARAMS,true,SourceType.USER_DEFINED,parameters);
            println("PROTOTYPE " + f.getEntryPoint()+" "+f.getSignature());
        }
        runScript("AuditFunctions.java",getScriptArgs());
    }
}
