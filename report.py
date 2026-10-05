"""Export a scan summary to a CSV report."""
import csv
import time


def export_csv(summary, dest_path: str):
    with open(dest_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "File Name", "File Path", "File Type", "Size (bytes)",
            "Status", "Reason", "Destination Path", "Scan Time (s)",
        ])
        for r in summary.results:
            writer.writerow([
                r.filename, r.path, r.category, r.size,
                r.status.value if hasattr(r.status, "value") else r.status,
                r.reason, r.dest_path, f"{r.scan_duration:.4f}",
            ])
        writer.writerow([])
        writer.writerow(["SCAN SUMMARY"])
        writer.writerow(["Total Files", summary.total])
        writer.writerow(["Working Files", summary.working])
        writer.writerow(["Non-Working Files", summary.non_working])
        writer.writerow(["Unsupported Files", summary.unsupported])
        writer.writerow(["Error Files", summary.errors])
        writer.writerow(["Scan Time (s)", f"{summary.elapsed_seconds:.2f}"])
        writer.writerow(["Generated", time.strftime("%Y-%m-%d %H:%M:%S")])
