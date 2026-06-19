import { FormEvent, useState } from "react";
import { useDashboardStore } from "../../store/dashboardStore";

export function NaturalLanguageCommand() {
  const planTask = useDashboardStore((state) => state.planTask);
  const [prompt, setPrompt] = useState("");
  const [planning, setPlanning] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setPlanning(true);
    try {
      await planTask(prompt);
      setPrompt("");
    } finally {
      setPlanning(false);
    }
  };

  return (
    <form className="command" onSubmit={submit}>
      <div>
        <p className="eyebrow">NATURAL LANGUAGE PROFILE</p>
        <b>一句话创建可验证任务</b>
        <span>例如：对 PID 1234 做 8 秒 py-spy 采集，频率 77Hz</span>
      </div>
      <input value={prompt} onChange={(event) => setPrompt(event.target.value)} required placeholder="必须包含目标 PID" />
      <button className="primary" disabled={planning}>
        {planning ? "正在规划" : "解析并下发"}
      </button>
    </form>
  );
}
