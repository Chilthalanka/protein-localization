import torch
import torch.nn as nn
import torch.nn.functional as F


class ConvAvgPool(nn.Module):
    def __init__(self, embeddings_dim: int = 1024, dropout=0.25):
        super(ConvAvgPool, self).__init__()

        self.conv1 = nn.Conv1d(embeddings_dim, embeddings_dim, 3, stride=2, padding=1)
        self.conv2 = nn.Conv1d(embeddings_dim, 512, 3, stride=2, padding=1)
        self.conv3 = nn.Conv1d(512, 512, 3, stride=2, padding=1)

        self.linear = nn.Sequential(
            nn.Linear(512, 32),
            nn.Dropout(dropout),
            nn.ReLU(),
            nn.BatchNorm1d(32)
        )

        self.output = nn.Linear(32, 11)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch_size, embeddings_dim, sequence_length] embedding tensor that should be classified

        Returns:
            classification: [batch_size,output_dim] tensor with logits
        """
        o = F.relu(self.conv1(x))
        o = F.relu(self.conv2(o))
        o = F.relu(self.conv3(o))
        o = torch.mean(o, dim=-1)
        o = self.linear(o)
        return self.output(o)
