// Instrumented toy; heap bounded so HPROF tests fit the small build host.
import java.util.ArrayList;
public class JavaWorkload {
    static volatile double sink;
    static double javaFixtureHotspot(double value) {
        for (int i = 0; i < 100000; ++i) value = Math.sin(value) + Math.sqrt(value * value + 1.01);
        return value;
    }
    public static void main(String[] args) throws Exception {
        var retained = new ArrayList<byte[]>();
        while (true) {
            sink = javaFixtureHotspot(sink);
            retained.add(new byte[65536]);
            if (retained.size() > 128) retained.subList(0, 64).clear();
            Thread.sleep(1);
        }
    }
}
