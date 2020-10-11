import torch
import torch.nn as nn
import torch.nn.functional as F


class SecondAttention(nn.Module):
    def __init__(self, embeddings_dim=1024, output_dim=11, dropout=0.25, kernel_size=7):
        super(SecondAttention, self).__init__()

        self.conv1 = nn.Conv1d(embeddings_dim, embeddings_dim, kernel_size, stride=1, padding=kernel_size//2)

        self.linear = nn.Sequential(
            nn.Linear(embeddings_dim, 32),
            nn.Dropout(dropout),
            nn.ReLU(),
            nn.BatchNorm1d(32)
        )

        self.output = nn.Linear(32, output_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch_size, embeddings_dim, sequence_length] embedding tensor that should be classified

        Returns:
            classification: [batch_size,output_dim] tensor with logits
        """
        o = self.conv1(x)
        attention = F.softmax(o, dim=-1)
        o = torch.sum(x * attention, dim=-1)
        o = self.linear(o)
        return self.output(o)