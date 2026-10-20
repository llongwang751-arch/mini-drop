import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { List } from "../types";
export default function GroupScope({
  value,
  onChange,
}: {
  value: { group_id: string; visibility: string };
  onChange: (value: { group_id: string; visibility: string }) => void;
}) {
  const groups = useQuery({
    queryKey: ["groups"],
    queryFn: ({ signal }) =>
      api<List<{ id: string; name: string }>>("/groups", { signal }),
  });
  return (
    <div className="form-grid">
      <label className="field">
        <span>所属权限组</span>
        <select
          value={value.group_id}
          onChange={(e) =>
            onChange({
              group_id: e.target.value,
              visibility: e.target.value ? value.visibility : "private",
            })
          }
        >
          <option value="">个人诊断</option>
          {groups.data?.items?.map((g) => (
            <option value={g.id} key={g.id}>
              {g.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>可见范围</span>
        <select
          disabled={!value.group_id}
          value={value.visibility}
          onChange={(e) => onChange({ ...value, visibility: e.target.value })}
        >
          <option value="private">仅本人和已授权共享者</option>
          <option value="group">所属组成员</option>
        </select>
      </label>
    </div>
  );
}
