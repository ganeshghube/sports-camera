# AI Training & Deployment Fix Summary

## Issues Fixed

### 1. data.yaml Path Case Mismatch
**File**: `data/training_dataset/data.yaml`
**Problem**: Path had `C:/00_Ganesh/Sports-Camera/` (capital S) but filesystem is `C:/00_Ganesh/sports-camera/` (lowercase s)
**Fix**: Changed `path: C:/00_Ganesh/Sports-Camera/data/training_dataset` to `path: C:/00_Ganesh/sports-camera/data/training_dataset`

### 2. Missing Python Packages for Training
The `.venv` only had `pip` installed. Required packages for AI training:

```bash
pip install ultralytics opencv-python numpy psutil websocket-client
```

*Note: OpenVINO export requires the OpenVINO runtime. If using Intel GPU/CPU, install OpenVINO separately.*

### 3. Training Pipeline Requirements

#### Step 0: Capture and Label Frames
Before training, you need at least 5 labeled frames:
1. Start the application: `python run.py`
2. Go to Live Preview and start camera
3. Start tracking (AI detects the ball)
4. Use the training UI to capture frames (`/api/training/capture`)
5. Label each frame with ball bounding boxes (`/api/training/frames/{name}/label`)
6. Ensure at least 5 frames are labeled (check at `/api/training/frames`)

#### Step 1: Start Training
```bash
# From sports-camera folder
python run.py
# OR directly run training:
python -m backend.training_router  # (if running as module)
```

Or via API:
```
POST /api/training/run
Body: {"epochs": 30, "batch": 8}
```
This will:
- Build dataset from labeled frames
- Fine-tune the model using ultralytics YOLO
- Save to `runs/detect/cricket_ui_XXXX/weights/best.pt`

#### Step 2: Deploy Model
After training completes, deploy the model:
```bash
POST /api/training/deploy
```
This will:
- Export the model to OpenVINO format (.xml/.bin)
- Copy `.pt` to `models/best.pt`
- Copy OpenVINO model to `models/best_openvino_model/`
- Print success message

#### Step 3: Restart Tracker
After deployment, restart the AI tracker in the web UI to use the new model.

### 4. Training Configuration
The training uses these defaults (from training_router.py):
- **Epochs**: 30 (configurable via API)
- **Batch size**: 8 (configurable)
- **Learning rate**: 0.001
- **Freeze layers**: 10 (backbone frozen, head fine-tuned)
- **Dataset split**: 85% train, 15% val
- **Classes**: `cricketBall` only

### 5. Troubleshooting

#### Common Issues:

1. **"Need at least 5 frames with ball labels"**
   - Capture and label at least 5 frames with the ball bounding boxes
   - Check `/api/training/frames` to verify labeled count

2. **Training fails / returns error**
   - Ensure all Python packages are installed
   - Check the training log for specific error messages
   - Verify the data.yaml path is correct (just fixed)

3. **Deploy fails**
   - Make sure training completed successfully (check status)
   - Verify `best.pt` exists at `runs/detect/cricket_ui_XXXX/weights/best.pt`
   - Ensure OpenVINO can be imported (`pip install openvino` if needed)

4. **Model not taking effect after deploy**
   - Restart the AI tracker in the web UI
   - The tracker loads `models/best.pt` on start, and `DEVICE` from .env

### 6. Verification Commands

Check training status:
```bash
GET http://127.0.0.1:8000/api/training/status
```

List training frames:
```bash
GET http://127.0.0.1:8000/api/training/frames
```

### 7. Full Workflow Summary

```
1. python run.py                          # Start server + tracker
2. Capture & label 5+ frames via UI       # Use training HTML pages
3. POST /api/training/run                 # Start training (builds dataset + trains model)
4. Wait for training to complete (log shows "✅ Training complete!")
5. POST /api/training/deploy              # Export to OpenVINO and deploy
6. Restart AI tracker in UI                # New model active
```

## Files Modified
- `data/training_dataset/data.yaml` - Fixed path case from `Sports-Camera` to `sports-camera`

## Requirements
Ensure these packages are installed in the `.venv`:
- ultralytics
- opencv-python  
- numpy
- psutil
- websocket-client
- openvino (for deployment)