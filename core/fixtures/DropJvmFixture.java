import java.util.ArrayList;
import java.util.List;

/** Controlled live HotSpot target. No synthetic profiler output is emitted. */
public final class DropJvmFixture {
    private static volatile double result;
    private static final List<byte[]> retained = new ArrayList<>();
    private static final Object monitor = new Object();

    private static double transform(int seed) {
        double value = 0;
        for (int i = 1; i < 40_000; i++) value += Math.sqrt((i + seed) % 10_007);
        return value;
    }

    private static void allocate(int seed) {
        retained.add(new byte[16_384 + seed % 2_048]);
        if (retained.size() > 256) retained.subList(0, 128).clear();
    }

    public static void main(String[] args) throws InterruptedException {
        Thread contender = new Thread(() -> {
            while (!Thread.currentThread().isInterrupted()) {
                synchronized (monitor) { result += transform(17); }
                Thread.yield();
            }
        }, "drop-java-contender");
        contender.setDaemon(true);
        contender.start();
        System.out.println("DROP_JVM_FIXTURE_READY");
        System.out.flush();
        int seed = 0;
        while (true) {
            synchronized (monitor) {
                result = transform(seed);
                for (int batch = 0; batch < 32; batch++) allocate(seed++);
            }
            Thread.sleep(5);
        }
    }
}
