import matplotlib.pyplot as plt
import os
import sys

# Graphs CSV files from an SD card named ROBOT_SD on the same plot.
# Each file has a header row with column labels, then x,y data pairs.
# Files sharing the same x-axis label are overlaid; mismatched x labels raise an error.
#
# Example file format:
#   timestamp, velocity
#   1, 0
#   2, 0.7898
#   3, 3.458909

file_paths = [
    "/Volumes/ROBOT_SD/rotation.txt",
    # "/Volumes/ROBOT_SD/voltage.txt",
]
# file_paths = [
#     "/Volumes/ROBOT_SD/raw_vel.txt",
#     "/Volumes/ROBOT_SD/ema_vel.txt"
# ]
fig, ax = plt.subplots()
expected_x_label = None

for file_path in file_paths:
    path = os.path.expanduser(file_path)

    if not os.path.exists(path):
        print(f"Warning: {path} not found, skipping.")
        continue

    with open(path, "r") as file:
        lines = [line.strip() for line in file if line.strip()]

    if len(lines) < 2:
        print(f"Warning: {path} has no data rows, skipping.")

    header_cols = [col.strip() for col in lines[0].split(",")]

    # If there's only one column, treat the row index as the x-axis.
    index_as_x = len(header_cols) == 1
    if index_as_x:
        x_label = "index"
        y_labels = header_cols
    else:
        x_label = header_cols[0]
        y_labels = header_cols[1:]

    if expected_x_label is None:
        expected_x_label = x_label
    elif x_label != expected_x_label:
        raise ValueError(
            f"X-axis mismatch in {path}: expected '{expected_x_label}', got '{x_label}'"
        )

    x_vals = []
    y_vals = [[] for _ in y_labels]
    for i, line in enumerate(lines[1:]):
        parts = [p.strip() for p in line.split(",")]
        if index_as_x:
            x_vals.append(float(i))
            cols = parts
        else:
            x_vals.append(float(parts[0]))
            cols = parts[1:]
        for series_idx in range(len(y_labels)):
            y_vals[series_idx].append(float(cols[series_idx]))

    for y_label, series in zip(y_labels, y_vals):
        ax.plot(x_vals, series, label=y_label)

    


ax.set_xlabel(expected_x_label or "x")
ax.legend()
ax.set_title("Velocity Controller Data")
plt.tight_layout()
plt.show()
# remove last data so sd card can be re-run to capture new data

