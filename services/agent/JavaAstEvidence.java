import com.sun.source.tree.*;
import com.sun.source.util.*;
import javax.lang.model.element.*;
import javax.tools.*;
import java.nio.file.*;
import java.util.*;
import java.util.stream.*;

/** JDK compiler AST/symbol observations. Output is TSV with base64-encoded text fields. */
public class JavaAstEvidence {
  static String enc(Object o) { return Base64.getEncoder().encodeToString(String.valueOf(o == null ? "" : o).getBytes(java.nio.charset.StandardCharsets.UTF_8)); }
  static void emit(String... fields) { System.out.println(Arrays.stream(fields).map(JavaAstEvidence::enc).collect(Collectors.joining("\t"))); }
  public static void main(String[] args) throws Exception {
    if (args.length < 1) { System.err.println("Usage: JavaAstEvidence <java source root>"); System.exit(2); }
    Path root = Paths.get(args[0]).toAbsolutePath().normalize();
    if (!Files.isDirectory(root)) { System.err.println("Missing source directory"); System.exit(2); }
    List<Path> sources;
    try (Stream<Path> s = Files.walk(root)) { sources = s.filter(p -> p.toString().endsWith(".java")).sorted().limit(4000).collect(Collectors.toList()); }
    // Optional explicit file narrows call-flow analysis to the requested class.
    if (args.length > 2 && !args[2].isBlank()) {
      Path selected = Paths.get(args[2]).toAbsolutePath().normalize();
      if (!selected.startsWith(root) || !Files.isRegularFile(selected) || !selected.toString().endsWith(".java")) {
        System.err.println("Requested Java source is outside the project source root"); System.exit(2);
      }
      sources = List.of(selected);
    }
    JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
    if (compiler == null) { System.err.println("JDK compiler unavailable"); System.exit(2); }
    DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
    try (StandardJavaFileManager fm = compiler.getStandardFileManager(diagnostics, null, null)) {
      Iterable<? extends JavaFileObject> units = fm.getJavaFileObjectsFromPaths(sources);
      List<String> options = Arrays.asList("-proc:none", "-implicit:none", "-sourcepath", root.toString(), "-d", args.length > 1 ? args[1] : root.toString());
      JavacTask task = (JavacTask) compiler.getTask(null, fm, diagnostics, options, null, units);
      Iterable<? extends CompilationUnitTree> parsed = task.parse();
      try { task.analyze(); } catch (Exception ignored) { /* unresolved dependencies stay unresolved */ }
      Trees trees = Trees.instance(task);
      for (CompilationUnitTree unit : parsed) {
        String file = Paths.get(unit.getSourceFile().toUri()).toAbsolutePath().normalize().toString();
        LineMap lines = unit.getLineMap();
        new TreePathScanner<Void, Void>() {
          String owner = ""; String method = "";
          int line(Tree tree) { long p = trees.getSourcePositions().getStartPosition(unit, tree); return p < 0 ? -1 : (int)lines.getLineNumber(p); }
          @Override public Void visitClass(ClassTree node, Void unused) {
            String old = owner; owner = old.isEmpty() ? node.getSimpleName().toString() : old + "." + node.getSimpleName();
            super.visitClass(node, unused); owner = old; return null;
          }
          @Override public Void visitMethod(MethodTree node, Void unused) {
            String old = method; method = node.getName().toString();
            super.visitMethod(node, unused); method = old; return null;
          }
          @Override public Void visitMethodInvocation(MethodInvocationTree node, Void unused) {
            Element element = trees.getElement(getCurrentPath());
            String resolution = element instanceof ExecutableElement && element.getEnclosingElement().asType().getKind() != javax.lang.model.type.TypeKind.ERROR ? "SYMBOL_RESOLVED" : "UNRESOLVED";
            String target = "";
            if (element instanceof ExecutableElement) {
              Element declaring = element.getEnclosingElement();
              target = declaring.toString() + "." + element.getSimpleName();
            }
            emit("CALL", file, owner, method, String.valueOf(line(node)), node.getMethodSelect().toString(), resolution, target);
            // Local getter-to-setter transfer: direct nested getter invocation as setter argument.
            if (node.getArguments().size() == 1 && element instanceof ExecutableElement &&
                element.getSimpleName().toString().startsWith("set")) {
              ExpressionTree arg = node.getArguments().get(0);
              if (arg instanceof MethodInvocationTree) {
                MethodInvocationTree getter = (MethodInvocationTree) arg;
                Element source = trees.getElement(new TreePath(getCurrentPath(), getter));
                if (source instanceof ExecutableElement && source.getSimpleName().toString().startsWith("get")) {
                  emit("TRANSFER", file, owner, method, String.valueOf(line(node)), getter.toString(),
                       "LOCAL_DIRECT_ARGUMENT", node.getMethodSelect().toString());
                }
              }
            }
            return super.visitMethodInvocation(node, unused);
          }
          @Override public Void visitIf(IfTree node, Void unused) {
            emit("CONDITION", file, owner, method, String.valueOf(line(node)), node.getCondition().toString(), "AST_PARSED", node.getElseStatement() == null ? "IF_TRUE_ONLY" : "IF_TRUE_ELSE");
            return super.visitIf(node, unused);
          }
          @Override public Void visitSwitch(SwitchTree node, Void unused) {
            emit("CONDITION", file, owner, method, String.valueOf(line(node)), node.getExpression().toString(), "AST_PARSED", "SWITCH");
            return super.visitSwitch(node, unused);
          }
          @Override public Void visitWhileLoop(WhileLoopTree node, Void unused) {
            emit("CONDITION", file, owner, method, String.valueOf(line(node)), node.getCondition().toString(), "AST_PARSED", "WHILE");
            return super.visitWhileLoop(node, unused);
          }
        }.scan(unit, null);
      }
    }
  }
}
