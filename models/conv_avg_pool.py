import torch
import torch.nn as nn


class ConvAvgPool(nn.Module):
    def __init__(self):
        super(ConvAvgPool, self).__init__()

        self.conv_block1 = nn.Sequential(
            nn.Conv1d(1024, 1024, 3, stride=1),
            nn.Sigmoid(),
            nn.Conv1d(1024, 1024, 3, stride=1),
            nn.Sigmoid()
        )

        self.conv_block2 = nn.Sequential(
            nn.Conv1d(1024, 1024, 3, stride=1),
            nn.Sigmoid(),
            nn.Conv1d(1024, 1024, 3, stride=1),
            nn.Sigmoid()
        )

        self.linear = nn.Sequential(
            nn.Linear(1024, 32),
            nn.Dropout(0.25),
            nn.ReLU()
        )

        self.output = nn.Linear(32, 10)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch_size, sequence_length, embeddings_dim] embedding tensor that should be classified

        Returns:
            classification: [batch_size,output_dim] tensor with logits
        """
        x = x.permute(0, 2, 1)
        x = self.conv_block1(x)
        x = self.conv_block2(x)
        x = torch.mean(x, dim=-1)
        x = self.linear(x)
        return self.output(x)
