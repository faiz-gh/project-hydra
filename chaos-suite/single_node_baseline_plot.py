import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

# Read data
df = pd.read_csv("./exports/baseline_single_node.csv")

# Set style
sns.set_style("whitegrid")
plt.figure(figsize=(10, 6), dpi=150)

# Create barplot with custom width
ax = sns.barplot(
    data=df,
    x="concurrency",
    y="tps",
    color="tab:green",
    errorbar=None,
    width=0.4,  # Make bars slimmer (half of standard default width)
    label="Single Node (Local)",
)

# Set throughput axis limit to 4000 TPS
plt.ylim(0, 4000)

# Customize axes and title
plt.title("Throughput Scaling (Single Node)", fontsize=14, fontweight="bold")
plt.xlabel("Concurrent Workers", fontsize=12)
plt.ylabel("Throughput (TPS)", fontsize=12)

# Add legend
plt.legend(loc="upper right", fontsize=11)

# Save the plot
plt.tight_layout()
plt.savefig("./exports/graphs/single_node_plot.png")
print("Successfully generated and saved modified plot.")
