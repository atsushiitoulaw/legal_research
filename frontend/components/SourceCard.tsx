export default function SourceCard({ title, content }: { title: string; content: string }) {
return (
<div className="border border-gray-300 rounded p-3 mb-3 bg-gray-50 text-left">
<div className="font-bold text-blue-700 text-sm mb-1">📄 {title}</div>
<div className="text-xs text-gray-600 leading-relaxed">{content}</div>
</div>
);
}