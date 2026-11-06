"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { fetchModelChecks, type ModelCheck } from "@/lib/api";

function statusLabel(item: ModelCheck) {
  if (item.availability === "expired") return "已过期 · 待复测";
  if (!item.usable && item.status.startsWith("image_generated")) return "已出图 · 型号未确认";
  if (item.status === "responded") return "文本通过";
  if (item.status === "responded_with_different_model") return "返回了其他模型";
  if (item.status.startsWith("image_generated")) return "已出图";
  return item.http_status ? `未通过 · HTTP ${item.http_status}` : "未通过";
}

export function ModelChecksCard({ onRefresh }: { onRefresh?: () => void }) {
  const [items, setItems] = useState<ModelCheck[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const load = async (notify = false) => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchModelChecks();
      setItems(data.items);
      if (notify) onRefresh?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取实测结果失败");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, []);

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="p-4">
        <div className="mb-3 flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-medium text-stone-700">模型实测结果</div>
            <p className="mt-1 text-xs leading-5 text-stone-500">记录当前账号的调用结果和上游返回标识。图片内部版本由上游决定；更换账号或上游更新后，结果可能变化。</p>
          </div>
          <Button variant="outline" size="sm" onClick={() => void load(true)} disabled={loading}>刷新结果</Button>
        </div>
        {error ? <p className="text-sm text-rose-600">{error}</p> : items.length === 0 ? (
          <p className="text-sm text-stone-400">{loading ? "正在读取..." : "暂无实测记录"}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-stone-400"><tr><th className="py-2 pr-4 font-medium">请求模型</th><th className="pr-4 font-medium">结果</th><th className="pr-4 font-medium">上游返回模型</th><th className="font-medium">测试时间</th></tr></thead>
              <tbody>{items.map((item) => (
                <tr key={item.model} className="border-t border-stone-100">
                  <td className="whitespace-nowrap py-2 pr-4 font-mono">{item.model}</td>
                  <td className={`whitespace-nowrap pr-4 ${item.usable ? "text-emerald-700" : "text-amber-700"}`}>{statusLabel(item)}</td>
                  <td className="pr-4 font-mono text-stone-600">{item.actual_models.join(", ") || "未返回"}</td>
                  <td className="whitespace-nowrap text-stone-400">{item.checked_at ? new Date(item.checked_at).toLocaleString("zh-CN") : "未知"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
