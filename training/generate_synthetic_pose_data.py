import os
import numpy as np

def generate_synthetic_poses():
    print("Generating synthetic head pose dataset...")
    # Define directories
    data_dir = os.path.join("datasets", "head_pose")
    os.makedirs(data_dir, exist_ok=True)
    
    # 1. Generate normal training dataset:
    # Most points are centered around looking forward (pitch ~ 0, yaw ~ 0, roll ~ 0)
    # with a standard deviation of 3-5 degrees.
    np.random.seed(42)
    num_train_samples = 1000
    normal_pitch = np.random.normal(0, 3, num_train_samples)
    normal_yaw = np.random.normal(0, 4, num_train_samples)
    normal_roll = np.random.normal(0, 2, num_train_samples)
    
    train_data = np.stack([normal_pitch, normal_yaw, normal_roll], axis=1)
    train_path = os.path.join(data_dir, "normal_train.csv")
    np.savetxt(train_path, train_data, delimiter=",", fmt="%.4f")
    print(f"Saved {num_train_samples} normal training samples to {train_path}")

    # 2. Generate validation dataset:
    # Mix of normal and anomalous head poses.
    num_val_samples = 200
    val_pitch = np.random.normal(0, 3, num_val_samples)
    val_yaw = np.random.normal(0, 4, num_val_samples)
    val_roll = np.random.normal(0, 2, num_val_samples)
    
    # Inject some anomalies (candidate looking far left, right, up, down)
    # e.g., 10% anomalies
    num_anomalies = int(num_val_samples * 0.1)
    anomalous_indices = np.random.choice(num_val_samples, num_anomalies, replace=False)
    for idx in anomalous_indices:
        val_pitch[idx] = np.random.choice([-25, 30]) # Looking down/up extremely
        val_yaw[idx] = np.random.choice([-40, 45])   # Looking left/right extremely
        val_roll[idx] = np.random.normal(0, 5)
        
    val_data = np.stack([val_pitch, val_yaw, val_roll], axis=1)
    val_path = os.path.join(data_dir, "val.csv")
    np.savetxt(val_path, val_data, delimiter=",", fmt="%.4f")
    print(f"Saved {num_val_samples} validation samples (with anomalies) to {val_path}")
    print("Done!")

if __name__ == "__main__":
    generate_synthetic_poses()
